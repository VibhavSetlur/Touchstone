"""The Gateway — the single legal path from a QA capability to a connector.

Every tool call passes through `Gateway.execute(ctx)`. The pipeline:

    1. Resolve tenant.       (tenant_id from transport, not from AI input)
    2. Resolve connection.   (must be in tenant's manifest)
    3. Rate-limit.           (per-assistant, per-minute)
    4. Concurrency-cap.      (per-assistant in-flight queries)
    5. Static SQL guard.     (refuse cross-joins, auto-inject LIMIT)
    6. Policy evaluation.    (allow / deny / consent-required)
    7. Consent gate.         (block until approved)
    8. Cost preflight.       (EXPLAIN-based row/byte estimate)
    9. Snapshot wrap.        (if a snapshot transaction is active for this conn)
   10. Connector call.       (ONLY call site of the connector)
   11. Result truncation.    (belt-and-suspenders cap)
   12. PII scan.             (column-name + regex + Presidio + local NER)
   13. Mask.                 (PII findings + operator sensitivity catalog)
   14. Audit write.          (signed, hash-chained, tenant-tagged)
   15. Concurrency release.

Each step is defensive — failure halts the pipeline before touching the DB.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlglot

from touchstone.config import Config, ConnectionConfig
from touchstone.security.audit import AuditLogger
from touchstone.security.consent import ConsentGate
from touchstone.security.cost_guard import (
    ConcurrencyGate,
    CostGuard,
    CostGuardError,
    CostLimits,
)
from touchstone.security.masker import Masker, MaskedResult
from touchstone.security.pii import PIIDetector
from touchstone.security.policy import PolicyEngine
from touchstone.security.rate_limit import RateLimiter
from touchstone.security.sensitivity import SensitivityRegistry
from touchstone.security.snapshot import (
    Snapshot,
    SnapshotManager,
    rewrite_sql_with_time_travel,
)
from touchstone.security.tenant import (
    DEFAULT_TENANT,
    ConnectorPool,
    TenantRegistry,
)
from touchstone.types import (
    AuditRecord,
    ConnectorError,
    Engine,
    PolicyDecision,
    PolicyDeniedError,
    QueryResult,
    RateLimitedError,
    Verdict,
)


@dataclass(slots=True)
class ToolCallContext:
    assistant_id: str
    assistant_session: str
    tool: str
    connection: str
    sql: str
    params: dict[str, Any] = field(default_factory=dict)
    tenant_id: str = DEFAULT_TENANT
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GatewayOutcome:
    masked: MaskedResult
    audit_record: AuditRecord
    snapshot_ts: str | None = None
    warnings: list[str] = field(default_factory=list)


class Gateway:
    def __init__(
        self,
        config: Config,
        policy: PolicyEngine,
        pii: PIIDetector,
        masker: Masker,
        consent: ConsentGate,
        rate_limiter: RateLimiter,
        audit: AuditLogger,
        *,
        cost_guard: CostGuard | None = None,
        sensitivity: SensitivityRegistry | None = None,
        tenants: TenantRegistry | None = None,
        connector_pool: ConnectorPool | None = None,
        snapshot_manager: SnapshotManager | None = None,
    ) -> None:
        self.config = config
        self.policy = policy
        self.pii = pii
        self.masker = masker
        self.consent = consent
        self.rate_limiter = rate_limiter
        self.audit = audit
        self.cost_guard = cost_guard or CostGuard(limits=CostLimits())
        self.sensitivity = sensitivity or SensitivityRegistry()
        self.tenants = tenants or TenantRegistry()
        self.pool = connector_pool or ConnectorPool()
        self.snapshots = snapshot_manager or SnapshotManager()

    def execute(self, ctx: ToolCallContext) -> GatewayOutcome:
        start_ts = datetime.now(UTC)
        start_perf = time.perf_counter()
        warnings: list[str] = []

        # 1. Tenant resolution.
        manifest = self.tenants.get(ctx.tenant_id)

        # 2. Connection resolution + tenant-scoped allowlist.
        conn_cfg = self.config.connections.get(ctx.connection)
        if conn_cfg is None:
            raise PolicyDeniedError(f"unknown connection: {ctx.connection!r}")
        if (manifest.connections and ctx.connection not in manifest.connections
            and ctx.tenant_id != DEFAULT_TENANT):
            raise PolicyDeniedError(
                f"connection {ctx.connection!r} is not in tenant "
                f"{ctx.tenant_id!r}'s manifest."
            )

        # 3. Rate limit.
        rl_key = f"{ctx.tenant_id}/{ctx.assistant_id}"
        if not self.rate_limiter.allow(rl_key):
            raise RateLimitedError(
                f"assistant {ctx.assistant_id!r} (tenant {ctx.tenant_id!r}) "
                f"exceeded {self.config.security.rate_limit_per_minute} calls/min"
            )

        # 4. Concurrency cap.
        self.cost_guard.gate.acquire(rl_key)

        try:
            # 5. Static SQL guard (may rewrite the SQL — e.g. auto-LIMIT).
            sql, static_warnings = self._static_guard(ctx, conn_cfg)
            warnings.extend(static_warnings)

            # 6. Parse for policy evaluation.
            ast_summary = _summarize_sql(sql, conn_cfg)

            # 7. Policy evaluation.
            decision = self.policy.evaluate(
                assistant_id=ctx.assistant_id, connection=conn_cfg,
                tool=ctx.tool, sql_summary=ast_summary, metadata={**ctx.metadata,
                "tenant_id": ctx.tenant_id},
            )
            if decision.verdict == Verdict.DENY:
                self._audit_denied(start_ts, ctx, conn_cfg, ast_summary, decision)
                raise PolicyDeniedError(
                    decision.reason or f"policy {decision.matched_rule!r} denied this call"
                )

            # 8. Consent gate.
            if decision.verdict == Verdict.CONSENT_REQUIRED:
                granted = self.consent.request(
                    assistant_id=ctx.assistant_id, connection=ctx.connection,
                    tool=ctx.tool, sql=sql,
                    reason=decision.reason or "policy requires consent",
                    timeout_seconds=self.config.security.consent_timeout_seconds,
                )
                if not granted:
                    self._audit_denied(start_ts, ctx, conn_cfg, ast_summary, decision)
                    raise PolicyDeniedError("consent denied or timed out")

            # 9. Get tenant-scoped connector.
            connector = self.pool.get(ctx.tenant_id, conn_cfg)

            # 10. Cost preflight (EXPLAIN). Non-fatal on EXPLAIN errors; fatal
            #     if the estimate exceeds limits.
            try:
                estimate = self.cost_guard.explain_preflight(connector, sql, conn_cfg.engine)
                if estimate.note:
                    warnings.append(f"preflight: {estimate.note}")
            except CostGuardError:
                self._audit_denied(start_ts, ctx, conn_cfg, ast_summary,
                                   PolicyDecision(verdict=Verdict.DENY,
                                                  matched_rule="cost_guard",
                                                  reason="exceeded cost limits"))
                raise

            # 11. Snapshot rewrite (TOCTOU defense).
            active_snap = self.snapshots.active(ctx.tenant_id, ctx.connection)
            if active_snap is not None and active_snap.time_travel_clause:
                sql = rewrite_sql_with_time_travel(sql, conn_cfg.engine, active_snap)
                warnings.append(f"snapshot pinned at {active_snap.snapshot_ts}")

            # 12. Execute.
            try:
                result = connector.execute(sql, ctx.params)
            except ConnectorError:
                raise

            # 13. Enforce caps belt+suspenders.
            result = _enforce_caps(result, conn_cfg)

            # 14. PII scan.
            findings = self.pii.scan(result)

            # 15. Mask (with operator sensitivity catalog).
            catalog = self.sensitivity.for_connection(ctx.connection)
            qmap = self._qualified_columns(result, conn_cfg)
            masker = Masker(
                default_strategy=self.masker.default_strategy,
                per_entity=self.masker.per_entity,
                per_column=self.masker.per_column,
                sensitivity=catalog,
            )
            masked = masker.apply(result, findings, qualified_column_names=qmap)

            # 16. Audit.
            record = self._build_audit(
                start_ts=start_ts, ctx=ctx, conn_cfg=conn_cfg,
                ast_summary=ast_summary, decision=decision, masked=masked,
                latency_ms=(time.perf_counter() - start_perf) * 1000.0,
                snapshot_ts=active_snap.snapshot_ts if active_snap else None,
                warnings=warnings,
            )
            self.audit.write(record)

            return GatewayOutcome(
                masked=masked, audit_record=record,
                snapshot_ts=(active_snap.snapshot_ts if active_snap else None),
                warnings=warnings,
            )
        finally:
            self.cost_guard.gate.release(rl_key)

    # -- snapshot helper ---------------------------------------------------

    def snapshot(self, *, tenant_id: str, connection: str):
        """Context manager: pin a snapshot for the duration. All gateway
        calls within the `with:` block see the same MVCC view (or, on
        Snowflake/BQ/Databricks, the same time-travel timestamp).

        Usage:
            with gateway.snapshot(tenant_id=tid, connection="prod-ro"):
                profile = profile_table(...)         # at T0
                diff    = diff_environments(...)      # also at T0, guaranteed
        """
        conn_cfg = self.config.connections.get(connection)
        if conn_cfg is None:
            raise PolicyDeniedError(f"unknown connection: {connection!r}")
        connector = self.pool.get(tenant_id, conn_cfg)
        return self.snapshots.begin(
            tenant_id=tenant_id, connection_name=connection,
            connector=connector, engine=conn_cfg.engine,
        )

    # -- private helpers ---------------------------------------------------

    def _static_guard(self, ctx: ToolCallContext,
                      conn_cfg: ConnectionConfig) -> tuple[str, list[str]]:
        return self.cost_guard.static_check(
            ctx.sql, conn_cfg.engine, conn_cfg.tags,
        )

    def _qualified_columns(self, result: QueryResult,
                            conn_cfg: ConnectionConfig) -> dict[str, str]:
        """Best-effort: map each column name to a qualified form like
        `schema.table.column` so the sensitivity catalog can match globs.

        For now, we only have the SELECT-projection name; full lineage
        resolution is a roadmap item. Operators who want strict matching
        should target patterns that work on the projection name itself
        (e.g. `email`, `notes`, `user_notes`).
        """
        schema = conn_cfg.schema_ or ""
        prefix = (schema + ".") if schema else ""
        return {c.name: f"{prefix}{c.name}" for c in result.columns}

    def _audit_denied(self, start_ts, ctx, conn_cfg, ast_summary, decision):
        self.audit.write(AuditRecord(
            ts=start_ts, assistant_id=ctx.assistant_id,
            assistant_session=ctx.assistant_session, tool=ctx.tool,
            connection=conn_cfg.name, sql_hash=_hash(ctx.sql),
            sql_ast_summary={**ast_summary, "tenant_id": ctx.tenant_id},
            policy_verdict=decision,
        ))

    def _build_audit(self, start_ts, ctx, conn_cfg, ast_summary, decision,
                     masked, latency_ms, snapshot_ts=None, warnings=None):
        pii_summary: dict[str, int] = {}
        for f in masked.findings:
            pii_summary[f.entity_type] = pii_summary.get(f.entity_type, 0) + 1
        ast = dict(ast_summary)
        ast["tenant_id"] = ctx.tenant_id
        if snapshot_ts:
            ast["snapshot_ts"] = snapshot_ts
        if warnings:
            ast["warnings"] = warnings
        return AuditRecord(
            ts=start_ts, assistant_id=ctx.assistant_id,
            assistant_session=ctx.assistant_session, tool=ctx.tool,
            connection=conn_cfg.name, sql_hash=_hash(ctx.sql),
            sql_ast_summary=ast, policy_verdict=decision,
            pii_summary=pii_summary, rows=masked.result.row_count,
            cells=masked.result.row_count * len(masked.result.columns),
            bytes_returned=masked.result.bytes_returned, latency_ms=latency_ms,
            sample_masked=masked.result.to_dicts()[:3],
        )


def _enforce_caps(result: QueryResult, conn_cfg: ConnectionConfig) -> QueryResult:
    if len(result.rows) > conn_cfg.row_cap:
        result.rows = result.rows[: conn_cfg.row_cap]
        result.row_count = conn_cfg.row_cap
        result.truncated = True
    return result


def _summarize_sql(sql: str, conn_cfg: ConnectionConfig) -> dict[str, Any]:
    if conn_cfg.engine.value == "mongodb":
        return {"mongo_spec": sql, "kind": "mongo", "is_select": True, "touches": []}
    try:
        dialect = {
            "postgres": "postgres", "mysql": "mysql", "snowflake": "snowflake",
            "bigquery": "bigquery", "databricks": "databricks", "redshift": "redshift",
            "trino": "trino", "clickhouse": "clickhouse", "duckdb": "duckdb",
            "sqlite": "sqlite", "files": "duckdb",
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
        "kind": kind, "is_select": is_select, "touches": touches,
        "has_limit": stmt.find(sqlglot.exp.Limit) is not None,
        "joins": len(list(stmt.find_all(sqlglot.exp.Join))),
    }


def _full_name(t) -> str:
    parts = [p for p in (t.catalog, t.db, t.name) if p]
    return ".".join(parts)


def _hash(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()
