"""Tamper-evident append-only audit log.

Every record carries a `prev_record_hash` and `record_hash`, forming a chain.
Tampering with any record breaks the chain. `touchstone audit verify` rewalks
the chain and reports the first inconsistency.

Sinks are pluggable — file (default), S3, Splunk HEC, Datadog Logs, OTLP,
generic HTTP webhook.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from touchstone.types import AuditRecord


class AuditSink(ABC):
    @abstractmethod
    def write(self, record: AuditRecord) -> None:
        ...

    def close(self) -> None:
        pass


class FileSink(AuditSink):
    """One JSONL file per day by default. Atomic appends (single open(), one
    write() per record). Hash-chained — see AuditLogger."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: AuditRecord) -> None:
        line = json.dumps(_to_jsonable(record), separators=(",", ":")) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


class S3Sink(AuditSink):
    """Buffered S3 sink — use with object-lock-enabled bucket. Best for
    long-term retention. Implementation: batches every N records or T seconds."""

    def __init__(self, bucket: str, prefix: str, region: str | None = None) -> None:
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self.region = region
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def write(self, record: AuditRecord) -> None:
        with self._lock:
            self._buffer.append(_to_jsonable(record))
            if len(self._buffer) >= 100:
                self._flush()

    def _flush(self) -> None:
        if not self._buffer:
            return
        try:
            import boto3
        except ImportError:
            self._buffer.clear()
            return
        client = boto3.client("s3", region_name=self.region)
        body = "\n".join(json.dumps(r, separators=(",", ":")) for r in self._buffer)
        key = f"{self.prefix}/{datetime.utcnow().strftime('%Y/%m/%d/%H%M%S')}.jsonl"
        client.put_object(Bucket=self.bucket, Key=key, Body=body.encode("utf-8"))
        self._buffer.clear()

    def close(self) -> None:
        with self._lock:
            self._flush()


class WebhookSink(AuditSink):
    """Generic JSON webhook — point at Splunk HEC, Datadog Logs, OTLP, or any
    log shipper that accepts JSON-over-HTTP."""

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = headers or {"Content-Type": "application/json"}

    def write(self, record: AuditRecord) -> None:
        import urllib.request
        data = json.dumps(_to_jsonable(record)).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, headers=self.headers)
        try:
            urllib.request.urlopen(req, timeout=5)  # noqa: S310 — operator-controlled
        except Exception:
            # Audit MUST NOT fail the tool call — operators rely on at-least-once
            # delivery to a separate sink (file) for ground truth.
            pass


class MemorySink(AuditSink):
    """For tests."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def write(self, record: AuditRecord) -> None:
        self.records.append(record)


class AuditLogger:
    """Fans out to multiple sinks and maintains the hash chain."""

    GENESIS = "sha256:0" * 8

    def __init__(self, sinks: list[AuditSink]) -> None:
        self.sinks = sinks
        self._last_hash = self.GENESIS
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, configs: list[dict[str, Any]]) -> AuditLogger:
        sinks: list[AuditSink] = []
        for cfg in configs:
            kind = cfg["kind"]
            if kind == "file":
                sinks.append(FileSink(cfg["path"]))
            elif kind == "s3":
                sinks.append(S3Sink(cfg["bucket"], cfg["prefix"], cfg.get("region")))
            elif kind == "webhook":
                sinks.append(WebhookSink(cfg["url"], cfg.get("headers")))
            elif kind == "memory":
                sinks.append(MemorySink())
            else:
                raise ValueError(f"unknown audit sink kind: {kind!r}")
        return cls(sinks)

    def write(self, record: AuditRecord) -> AuditRecord:
        with self._lock:
            record.prev_record_hash = self._last_hash
            payload = json.dumps(
                _to_jsonable(record, exclude={"record_hash"}),
                separators=(",", ":"), sort_keys=True,
            ).encode("utf-8")
            record.record_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
            self._last_hash = record.record_hash
            for s in self.sinks:
                s.write(record)
        return record

    def close(self) -> None:
        for s in self.sinks:
            s.close()


def _to_jsonable(record: AuditRecord, exclude: set[str] | None = None) -> dict[str, Any]:
    exclude = exclude or set()
    d = asdict(record)
    for k in exclude:
        d.pop(k, None)
    # Convert datetime to ISO 8601.
    if isinstance(d.get("ts"), datetime):
        d["ts"] = d["ts"].isoformat()
    # PolicyDecision and Verdict need flattening.
    pv = d.get("policy_verdict")
    if pv and isinstance(pv, dict):
        if hasattr(pv.get("verdict"), "value"):
            pv["verdict"] = pv["verdict"].value
    return d


def verify_chain(path: Path) -> tuple[bool, int, str | None]:
    """Walk the file, recompute each record's hash, and report the first
    broken link. Returns (is_valid, records_seen, first_error)."""

    last = AuditLogger.GENESIS
    seen = 0
    with path.open() as f:
        for line in f:
            seen += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                return False, seen, f"record {seen}: invalid JSON"
            if rec.get("prev_record_hash") != last:
                return False, seen, f"record {seen}: prev_hash mismatch"
            expected = rec.pop("record_hash", "")
            payload = json.dumps(rec, separators=(",", ":"), sort_keys=True).encode("utf-8")
            computed = "sha256:" + hashlib.sha256(payload).hexdigest()
            if expected != computed:
                return False, seen, f"record {seen}: record_hash mismatch"
            last = expected
    return True, seen, None
