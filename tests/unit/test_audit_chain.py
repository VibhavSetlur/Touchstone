"""Audit log chain tests — write a few records, then verify the chain holds
and that tampering breaks it."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from touchstone.security.audit import AuditLogger, FileSink, verify_chain
from touchstone.types import AuditRecord, PolicyDecision, Verdict


def _record(i: int) -> AuditRecord:
    return AuditRecord(
        ts=datetime(2026, 6, 1, 12, i % 60, tzinfo=UTC),
        assistant_id="x", assistant_session="s", tool="query_database",
        connection="c", sql_hash=f"sha256:{i:064d}", sql_ast_summary={"i": i},
        policy_verdict=PolicyDecision(verdict=Verdict.PERMIT, matched_rule="allow-all"),
        rows=i, cells=i, bytes_returned=i, latency_ms=float(i),
    )


def test_chain_valid_when_untouched(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(sinks=[FileSink(path)])
    for i in range(5):
        logger.write(_record(i))
    ok, seen, err = verify_chain(path)
    assert ok, err
    assert seen == 5


def test_chain_broken_when_record_modified(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(sinks=[FileSink(path)])
    for i in range(3):
        logger.write(_record(i))

    lines = path.read_text().splitlines()
    # Tamper with the middle record's rows field.
    rec = json.loads(lines[1])
    rec["rows"] = 999
    lines[1] = json.dumps(rec, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    ok, seen, err = verify_chain(path)
    assert not ok
    assert err is not None
    assert "record 2" in err
