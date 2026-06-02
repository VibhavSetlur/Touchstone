"""Pre-execution cost guards.

An LLM that means well can still write a query that takes down the warehouse.
Examples:

  - `SELECT COUNT(DISTINCT user_id) FROM events_5b_rows` — full scan + sort.
  - `SELECT * FROM big_table CROSS JOIN small_table` — Cartesian explosion.
  - `SELECT * FROM events` (no LIMIT) — billions of rows back across the wire.

Touchstone defends with three independent checks, in order:

  1. **Static SQL guard** (sqlglot AST): refuse cross joins, refuse SELECT *
     without LIMIT on tables tagged `large`, auto-inject `LIMIT row_cap` if
     the query is unbounded.
  2. **EXPLAIN preflight**: ask the engine what this query will cost. Reject
     if estimated rows scanned > threshold, or estimated bytes scanned >
     `byte_budget`.
  3. **Concurrency cap**: per-assistant max in-flight queries. A panicked
     assistant can otherwise spawn 50 parallel deep scans.

Each engine implements EXPLAIN differently. We dispatch through a small
per-engine adapter; engines without a usable EXPLAIN fall back to the
static guard + the connector's row_cap + the per-assistant timeout.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import expressions as exp

from touchstone.types import Engine, TouchstoneError


class CostGuardError(TouchstoneError):
    code = "cost_guard/refused"


class ConcurrencyCapError(TouchstoneError):
    code = "cost_guard/concurrent_cap"


@dataclass(slots=True)
class CostEstimate:
    rows: int | None = None
    bytes_scanned: int | None = None
    cost_units: float | None = None  # vendor-specific (Snowflake credits, BQ slots)
    note: str = ""


@dataclass(slots=True)
class CostLimits:
    max_estimated_rows: int = 100_000_000           # 100M scan estimate
    max_estimated_bytes: int = 10 * 1024 ** 3        # 10 GB scan estimate
    refuse_cross_join: bool = True
    require_limit_on_large: bool = True
    large_tag: str = "large"
    auto_inject_limit: int = 10_000
    concurrent_cap_per_assistant: int = 4
    timeout_seconds: int = 30

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> CostLimits:
        return cls(**{k: v for k, v in raw.items()
                      if k in cls.__dataclass_fields__})


class ConcurrencyGate:
    """Per-assistant in-flight counter. Refuses when an assistant has too
    many open queries — typically a sign of a panicked retry loop."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self._lock = threading.Lock()
        self._inflight: dict[str, int] = {}

    def acquire(self, assistant_id: str) -> None:
        with self._lock:
            n = self._inflight.get(assistant_id, 0)
            if n >= self.cap:
                raise ConcurrencyCapError(
                    f"assistant {assistant_id!r} already has {n} in-flight queries "
                    f"(cap {self.cap}). Wait for one to finish or split the work."
                )
            self._inflight[assistant_id] = n + 1

    def release(self, assistant_id: str) -> None:
        with self._lock:
            n = self._inflight.get(assistant_id, 1)
            self._inflight[assistant_id] = max(0, n - 1)


class CostGuard:
    """Pre-flight checker."""

    def __init__(self, limits: CostLimits, large_tables: set[str] | None = None) -> None:
        self.limits = limits
        self.large_tables = large_tables or set()
        self.gate = ConcurrencyGate(limits.concurrent_cap_per_assistant)

    # -- 1. Static SQL guard ----------------------------------------------

    def static_check(self, sql: str, engine: Engine,
                     connection_tags: list[str]) -> tuple[str, list[str]]:
        """Returns (possibly rewritten SQL, warnings).

        Raises CostGuardError for unfixable problems (cross-join etc.).
        Auto-injects a LIMIT when missing.
        """
        warnings: list[str] = []
        dialect = _dialect_for_engine(engine)
        try:
            stmts = sqlglot.parse(sql, read=dialect)
        except sqlglot.errors.ParseError:
            # Can't parse — let the connector deal with it; no static guards apply.
            return sql, ["could not parse SQL for static checks"]

        if not stmts or stmts[0] is None:
            return sql, []
        stmt = stmts[0]

        # Cross-join refusal.
        if self.limits.refuse_cross_join:
            for join in stmt.find_all(exp.Join):
                kind = (join.args.get("kind") or "").upper()
                side = (join.args.get("side") or "").upper()
                # Detect explicit CROSS JOIN, or JOIN with no ON / USING.
                if kind == "CROSS" or (not join.args.get("on") and not join.args.get("using")
                                        and kind != "NATURAL"):
                    raise CostGuardError(
                        "cross-joins are refused by default. Add a JOIN ... ON ... "
                        "predicate, or set cost_limits.refuse_cross_join = false "
                        "for this connection."
                    )

        # SELECT * on large tables without LIMIT.
        is_select = isinstance(stmt, (exp.Select,)) or stmt.find(exp.Select) is not None
        has_limit = stmt.find(exp.Limit) is not None
        select = stmt.find(exp.Select) if not isinstance(stmt, exp.Select) else stmt

        if select and self.limits.require_limit_on_large and not has_limit:
            tables = {t.name for t in stmt.find_all(exp.Table)}
            big = tables & self.large_tables
            if big:
                raise CostGuardError(
                    f"query touches large table(s) {sorted(big)} without LIMIT. "
                    "Add a LIMIT clause or tag the table as small."
                )

        # Auto-inject LIMIT if unbounded and not aggregating.
        if (is_select and not has_limit
            and not _has_top_level_aggregation(stmt)
            and self.limits.auto_inject_limit > 0):
            limited = sql.rstrip().rstrip(";") + f" LIMIT {self.limits.auto_inject_limit}"
            warnings.append(
                f"auto-injected LIMIT {self.limits.auto_inject_limit} "
                "(query had no LIMIT and was not aggregating)"
            )
            return limited, warnings

        return sql, warnings

    # -- 2. EXPLAIN preflight ---------------------------------------------

    def explain_preflight(self, connector, sql: str, engine: Engine) -> CostEstimate:
        """Run an engine-appropriate EXPLAIN and turn the result into a
        CostEstimate. Failure to EXPLAIN is non-fatal — we degrade to the
        static check + row_cap."""
        try:
            adapter = _EXPLAINERS.get(engine)
            if adapter is None:
                return CostEstimate(note=f"no EXPLAIN adapter for {engine.value}")
            est = adapter(connector, sql)
        except Exception as e:
            return CostEstimate(note=f"EXPLAIN failed: {e}")
        # Enforce limits.
        if est.rows is not None and est.rows > self.limits.max_estimated_rows:
            raise CostGuardError(
                f"EXPLAIN estimates {est.rows:,} rows scanned "
                f"(limit {self.limits.max_estimated_rows:,}). Refusing."
            )
        if est.bytes_scanned is not None and est.bytes_scanned > self.limits.max_estimated_bytes:
            raise CostGuardError(
                f"EXPLAIN estimates {est.bytes_scanned:,} bytes scanned "
                f"(limit {self.limits.max_estimated_bytes:,}). Refusing."
            )
        return est


# ----- per-engine EXPLAIN adapters ----------------------------------------

def _explain_postgres(connector, sql: str) -> CostEstimate:
    out = connector.execute(f"EXPLAIN (FORMAT JSON) {sql}")
    if not out.rows:
        return CostEstimate()
    import json
    plan = out.rows[0][0]
    if isinstance(plan, str):
        plan = json.loads(plan)
    root = plan[0]["Plan"] if isinstance(plan, list) else plan["Plan"]
    return CostEstimate(
        rows=int(root.get("Plan Rows", 0)),
        cost_units=float(root.get("Total Cost", 0)),
        note="postgres EXPLAIN",
    )


def _explain_mysql(connector, sql: str) -> CostEstimate:
    out = connector.execute(f"EXPLAIN FORMAT=JSON {sql}")
    if not out.rows:
        return CostEstimate()
    import json
    plan = out.rows[0][0]
    if isinstance(plan, str):
        plan = json.loads(plan)
    qb = plan.get("query_block", {})
    # MySQL JSON EXPLAIN has nested structure; sum row_examined_per_scan across
    # tables, best-effort.
    rows = _mysql_walk_rows(qb)
    cost = float(qb.get("cost_info", {}).get("query_cost", 0) or 0)
    return CostEstimate(rows=rows, cost_units=cost, note="mysql EXPLAIN")


def _mysql_walk_rows(node) -> int:
    total = 0
    if isinstance(node, dict):
        if "rows_examined_per_scan" in node:
            total += int(node["rows_examined_per_scan"])
        for v in node.values():
            total += _mysql_walk_rows(v)
    elif isinstance(node, list):
        for item in node:
            total += _mysql_walk_rows(item)
    return total


def _explain_snowflake(connector, sql: str) -> CostEstimate:
    # Snowflake EXPLAIN USING TABULAR returns query plan; bytes scanned
    # estimate is in QUERY_HISTORY post-execution, not in EXPLAIN. We use
    # GET_QUERY_OPERATOR_STATS(LAST_QUERY_ID()) as a fallback but it's
    # post-hoc; for preflight, return a "best effort" note.
    out = connector.execute(f"EXPLAIN USING TABULAR {sql}")
    return CostEstimate(rows=None, note=f"snowflake EXPLAIN ({len(out.rows)} ops)")


def _explain_bigquery(connector, sql: str) -> CostEstimate:
    """BigQuery has a real dry-run that returns bytes processed for free."""
    try:
        from google.cloud import bigquery
    except ImportError:
        return CostEstimate(note="bigquery driver not installed")
    job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    job = connector._connection.query(sql, job_config=job_config)
    return CostEstimate(
        bytes_scanned=job.total_bytes_processed,
        note=f"bigquery dry-run: {job.total_bytes_processed:,} bytes",
    )


def _explain_duckdb(connector, sql: str) -> CostEstimate:
    out = connector.execute(f"EXPLAIN {sql}")
    return CostEstimate(rows=None, note="duckdb EXPLAIN (no row estimate)")


_EXPLAINERS = {
    Engine.POSTGRES: _explain_postgres,
    Engine.MYSQL: _explain_mysql,
    Engine.SNOWFLAKE: _explain_snowflake,
    Engine.BIGQUERY: _explain_bigquery,
    Engine.DUCKDB: _explain_duckdb,
    Engine.REDSHIFT: _explain_postgres,  # Redshift dialect close enough
}


# ----- helpers ------------------------------------------------------------

def _dialect_for_engine(engine: Engine) -> str:
    return {
        Engine.POSTGRES: "postgres", Engine.MYSQL: "mysql",
        Engine.SNOWFLAKE: "snowflake", Engine.BIGQUERY: "bigquery",
        Engine.DATABRICKS: "databricks", Engine.REDSHIFT: "redshift",
        Engine.TRINO: "trino", Engine.CLICKHOUSE: "clickhouse",
        Engine.DUCKDB: "duckdb", Engine.SQLITE: "sqlite",
    }.get(engine, "")


def _has_top_level_aggregation(stmt) -> bool:
    """True if the outermost SELECT is aggregating (GROUP BY, top-level
    aggregate function, DISTINCT). Aggregating queries shouldn't have LIMIT
    auto-injected because the LIMIT would change the semantic answer."""
    sel = stmt if isinstance(stmt, exp.Select) else stmt.find(exp.Select)
    if sel is None:
        return False
    if sel.args.get("group"):
        return True
    if sel.args.get("distinct"):
        return True
    for e in sel.expressions:
        # Walk into projection expressions for aggregate functions at the top.
        for fn in e.find_all(exp.AggFunc):
            return True
    return False
