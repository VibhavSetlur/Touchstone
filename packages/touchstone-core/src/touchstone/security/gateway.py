"""The Gateway — the single legal path from a QA capability to a connector.

Every tool call goes:

    QA capability
      └─► Gateway.execute(ctx)
            ├─► RateLimiter.check
            ├─► PolicyEngine.evaluate         → may deny / require consent
            ├─► ConsentGate.request_if_needed → may block, ask operator
            ├─► (connector.execute)           ← only call site of the connector
            ├─► result truncation
            ├─► PIIDetector.scan
            ├─► Masker.apply
            └─► AuditLogger.write

Refactoring tip: this method is intentionally long. Splitting into helpers
makes the trust pipeline harder to audit at a glance. Each step is one block.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlglot

from touchstone.config import Config, ConnectionConfig
from touchstone.connectors import get_connector
from touchstone.security.audit import AuditLogger
from touchstone.security.consent import ConsentGate
from touchstone.security.masker import Masker, MaskedResult
from touchstone.security.pii import PIIDetector
from touchstone.security.policy import PolicyEngine
from touchstone.security.rate_limit import RateLimiter
from touchstone.types import (
    AuditRecord,
    ConnectorError,
    PolicyDecision,
    PolicyDeniedError,
    QueryResult,
    RateLimitedError,
    Verdict,
)


@dataclass(slots=True)
class ToolCallContext:
    """Everything the gateway needs to know about one call."""

    assistant_id: str
    assistant_session: str
    tool: str
    connection: str
    sql: str
    params: dict[str, Any] = field(default_factory=dict)
    # Operator-provided context for policies — e.g. "this call came from the
    # GitHub App, here's the PR number".
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GatewayOutcome:
    masked: MaskedResult
    audit_record: AuditRecord


class Gateway:
    """The trust boundary. Holds one of each safety component."""

    def __init__(
        self,
        config: Config,
        policy: PolicyEngine,
        pii: PIIDetector,
        masker: Masker,
        consent: ConsentGate,
        rate_limiter: RateLimiter,
        audit: AuditLogger,
    ) -> None:
        self.config = config
        self.policy = policy
        self.pii = pii
        self.masker = masker
        self.consent = consent
        self.rate_limiter = rate_limiter
        self.audit = audit

    def execute(self, ctx: ToolCallContext) -> GatewayOutcome:
        start_ts = datetime.now(tz=UTC)
        start_perf = time.perf_counter()

        # 1. Resolve the connection from config.
        conn_cfg = self.config.connections.get(ctx.connection)
        if conn_cfg is None:
            raise PolicyDeniedError(f"unknown connection: {ctx.connection!r}")

        # 2. Rate-limit check (before any expensive parsing).
        if not self.rate_limiter.allow(ctx.assistant_id):
            raise RateLimitedError(
                f"assistant {ctx.assistant_id!r} exceeded "
                f"{self.config.security.rate_limit_per_minute} calls/minute"
            )

        # 3. Parse SQL into an analyzable form (for policy and audit). For
        #    non-SQL specs (Mongo), pass through unparsed.
        ast_summary = _summarize_sql(ctx.sql, conn_cfg)

        # 4. Policy evaluation.
        decision = self.policy.evaluate(
            assistant_id=ctx.assistant_id,
            connection=conn_cfg,
            tool=ctx.tool,
            sql_summary=ast_summary,
            metadata=ctx.metadata,
        )

        if decision.verdict == Verdict.DENY:
            self._audit_denied(start_ts, ctx, conn_cfg, ast_summary, decision)
            raise PolicyDeniedError(
                decision.reason or f"policy {decision.matched_rule!r} denied this call"
            )

        # 5. Consent gate (may block).
        if decision.verdict == Verdict.CONSENT_REQUIRED:
            granted = self.consent.request(
                assistant_id=ctx.assistant_id,
                connection=ctx.connection,
                tool=ctx.tool,
                sql=ctx.sql,
                reason=decision.reason or "policy requires consent",
                timeout_seconds=self.config.security.consent_timeout_seconds,
            )
            if not granted:
                self._audit_denied(start_ts, ctx, conn_cfg, ast_summary, decision)
                raise PolicyDeniedError("consent denied or timed out")

        # 6. Connector call — the ONLY place we touch the DB.
        connector = get_connector(conn_cfg)
        try:
            with connector:
                result = connector.execute(ctx.sql, ctx.params)
        except ConnectorError:
            raise

        # 7. Result is already row/byte-capped by the connector. Belt + suspenders:
        #    cap again here so a misbehaving connector can't slip past.
        result = _enforce_caps(result, conn_cfg)

        # 8. PII scan.
        findings = self.pii.scan(result)

        # 9. Mask.
        masked = self.masker.apply(result, findings)

        # 10. Audit.
        record = self._build_audit(
            start_ts=start_ts,
            ctx=ctx,
            conn_cfg=conn_cfg,
            ast_summary=ast_summary,
            decision=decision,
            masked=masked,
            latency_ms=(time.perf_counter() - start_perf) * 1000.0,
        )
        self.audit.write(record)

        return GatewayOutcome(masked=masked, audit_record=record)

    def _audit_denied(
        self,
        start_ts: datetime,
        ctx: ToolCallContext,
        conn_cfg: ConnectionConfig,
        ast_summary: dict[str, Any],
        decision: PolicyDecision,
    ) -> None:
        self.audit.write(AuditRecord(
            ts=start_ts,
            assistant_id=ctx.assistant_id,
            assistant_session=ctx.assistant_session,
            tool=ctx.tool,
            connection=conn_cfg.name,
            sql_hash=_hash(ctx.sql),
            sql_ast_summary=ast_summary,
            policy_verdict=decision,
        ))

    def _build_audit(
        self,
        start_ts: datetime,
        ctx: ToolCallContext,
        conn_cfg: ConnectionConfig,
        ast_summary: dict[str, Any],
        decision: PolicyDecision,
        masked: MaskedResult,
        latency_ms: float,
    ) -> AuditRecord:
        pii_summary: dict[str, int] = {}
        for f in masked.findings:
            pii_summary[f.entity_type] = pii_summary.get(f.entity_type, 0) + 1
        return AuditRecord(
            ts=start_ts,
            assistant_id=ctx.assistant_id,
            assistant_session=ctx.assistant_session,
            tool=ctx.tool,
            connection=conn_cfg.name,
            sql_hash=_hash(ctx.sql),
            sql_ast_summary=ast_summary,
            policy_verdict=decision,
            pii_summary=pii_summary,
            rows=masked.result.row_count,
            cells=masked.result.row_count * len(masked.result.columns),
            bytes_returned=masked.result.bytes_returned,
            latency_ms=latency_ms,
            sample_masked=masked.result.to_dicts()[:3],
        )


def _enforce_caps(result: QueryResult, conn_cfg: ConnectionConfig) -> QueryResult:
    if len(result.rows) > conn_cfg.row_cap:
        result.rows = result.rows[: conn_cfg.row_cap]
        result.row_count = conn_cfg.row_cap
        result.truncated = True
    return result


def _summarize_sql(sql: str, conn_cfg: ConnectionConfig) -> dict[str, Any]:
    """Parse SQL into a normalized summary the policy engine can reason about.

    For Mongo-style JSON specs, returns the raw spec under "mongo_spec".
    Parse failures are not fatal — policies that need parsed AST will deny by
    default in that case.
    """
    if conn_cfg.engine.value == "mongodb":
        return {"mongo_spec": sql, "kind": "mongo", "is_select": True, "touches": []}

    try:
        dialect = {
            "postgres": "postgres", "mysql": "mysql", "snowflake": "snowflake",
            "bigquery": "bigquery", "databricks": "databricks", "redshift": "redshift",
            "trino": "trino", "clickhouse": "clickhouse", "duckdb": "duckdb",
            "sqlite": "sqlite",
        }.get(conn_cfg.engine.value, "")
        statements = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError:
        return {"kind": "unparseable", "is_select": False, "touches": []}

    if not statements or not statements[0]:
        return {"kind": "empty", "is_select": False, "touches": []}

    stmt = statements[0]
    kind = type(stmt).__name__.upper()
    is_select = kind in {"SELECT", "WITH", "UNION", "EXCEPT", "INTERSECT"}
    touches = sorted({_full_name(t) for t in stmt.find_all(sqlglot.exp.Table)})

    return {
        "kind": kind,
        "is_select": is_select,
        "touches": touches,
        "has_limit": stmt.find(sqlglot.exp.Limit) is not None,
        "joins": len(list(stmt.find_all(sqlglot.exp.Join))),
    }


def _full_name(t: sqlglot.exp.Table) -> str:
    parts = [p for p in (t.catalog, t.db, t.name) if p]
    return ".".join(parts)


def _hash(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()
