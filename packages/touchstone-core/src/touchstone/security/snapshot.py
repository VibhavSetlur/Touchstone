"""Snapshot transactions — TOCTOU defense.

The Time-of-Check-to-Time-of-Use problem: an LLM profiles a table at T0,
generates a query based on that schema at T1, executes at T2. Between T0
and T2, a parallel dbt run / Airflow job / ETL pipeline can mutate the
table — different row count, retyped column, even a dropped table. The
LLM's "carefully reasoned" query then runs on a different reality and
either errors silently or returns nonsense.

The fix is engine-level snapshot isolation. Most modern engines support it
in some form:

  - PostgreSQL:  SET TRANSACTION ISOLATION LEVEL REPEATABLE READ; multiple
                 statements see the same MVCC snapshot.
  - Snowflake:   `AT(TIMESTAMP => ...)` / `AT(STATEMENT => '<query_id>')`
                 Time Travel — append per-table to every query.
  - BigQuery:    `FOR SYSTEM_TIME AS OF TIMESTAMP <ts>` table snapshot
                 decorator.
  - Databricks:  `TIMESTAMP AS OF <ts>` / `VERSION AS OF <version>` (Delta).
  - DuckDB:      transactional repeatable-read.
  - MySQL/InnoDB: REPEATABLE READ on a single transaction.
  - SQLite:      BEGIN IMMEDIATE locks once and stays consistent.

This module gives the gateway a `snapshot()` context manager that:

  - For multi-statement engines, opens a real transaction and pins isolation.
  - For Snowflake/BigQuery/Databricks, captures a snapshot timestamp at
    entry and rewrites every subsequent SQL to add the time-travel clause
    to each table reference.
  - Returns a `Snapshot` token in the result so the LLM can see
    `snapshot_ts="2026-06-02T18:42:17Z"` and reason about staleness.
  - For engines with no snapshot support, returns a best-effort token that
    just records the wall-clock time of the first call so downstream
    reports can flag "this report spanned X seconds; data may have shifted".
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterator

import sqlglot
from sqlglot import expressions as exp

from touchstone.types import Engine


@dataclass(slots=True)
class Snapshot:
    """A snapshot handle. Pass back to the gateway to scope queries."""

    engine: Engine
    snapshot_ts: str               # ISO8601 UTC of capture
    transaction_id: str | None = None  # for engines with real transactions
    time_travel_clause: str | None = None  # SQL fragment to inject per-table


class SnapshotManager:
    """Owns active snapshots per (tenant, connection)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[tuple[str, str], Snapshot] = {}

    @contextlib.contextmanager
    def begin(self, *, tenant_id: str, connection_name: str,
              connector: Any, engine: Engine) -> Iterator[Snapshot]:
        snap = _begin_snapshot(connector, engine)
        with self._lock:
            self._active[(tenant_id, connection_name)] = snap
        try:
            yield snap
        finally:
            _end_snapshot(connector, engine, snap)
            with self._lock:
                self._active.pop((tenant_id, connection_name), None)

    def active(self, tenant_id: str, connection_name: str) -> Snapshot | None:
        with self._lock:
            return self._active.get((tenant_id, connection_name))


def _begin_snapshot(connector: Any, engine: Engine) -> Snapshot:
    ts = datetime.now(UTC).isoformat()
    if engine == Engine.POSTGRES:
        connector.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return Snapshot(engine=engine, snapshot_ts=ts, transaction_id="pg_repeatable_read")
    if engine == Engine.MYSQL:
        connector.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        return Snapshot(engine=engine, snapshot_ts=ts, transaction_id="mysql_consistent_snapshot")
    if engine == Engine.SQLITE:
        connector.execute("BEGIN IMMEDIATE")
        return Snapshot(engine=engine, snapshot_ts=ts, transaction_id="sqlite_begin_immediate")
    if engine == Engine.DUCKDB:
        connector.execute("BEGIN TRANSACTION READ ONLY")
        return Snapshot(engine=engine, snapshot_ts=ts, transaction_id="duckdb_read_only")
    if engine == Engine.SNOWFLAKE:
        # Snowflake: capture timestamp, rewrite subsequent queries with AT(TIMESTAMP ...).
        return Snapshot(
            engine=engine, snapshot_ts=ts,
            time_travel_clause=f"AT(TIMESTAMP => '{ts}'::TIMESTAMP_TZ)",
        )
    if engine == Engine.BIGQUERY:
        return Snapshot(
            engine=engine, snapshot_ts=ts,
            time_travel_clause=f"FOR SYSTEM_TIME AS OF TIMESTAMP('{ts}')",
        )
    if engine == Engine.DATABRICKS:
        return Snapshot(
            engine=engine, snapshot_ts=ts,
            time_travel_clause=f"TIMESTAMP AS OF '{ts}'",
        )
    # Engines without snapshot support: best-effort timestamp.
    return Snapshot(engine=engine, snapshot_ts=ts)


def _end_snapshot(connector: Any, engine: Engine, snap: Snapshot) -> None:
    if engine in (Engine.POSTGRES, Engine.MYSQL, Engine.SQLITE, Engine.DUCKDB):
        try:
            connector.execute("ROLLBACK")
        except Exception:
            pass


def rewrite_sql_with_time_travel(sql: str, engine: Engine, snap: Snapshot) -> str:
    """For engines that use per-table time-travel decorators (Snowflake,
    BigQuery, Databricks), inject the clause after every base-table reference.

    Returns the rewritten SQL. For engines that use real transactions, the
    SQL passes through unchanged.
    """
    if not snap.time_travel_clause:
        return sql
    dialect = {
        Engine.SNOWFLAKE: "snowflake", Engine.BIGQUERY: "bigquery",
        Engine.DATABRICKS: "databricks",
    }.get(engine, "")
    try:
        stmts = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError:
        # If we can't parse, fall back to a regex insertion — best-effort.
        return _regex_inject_time_travel(sql, snap.time_travel_clause)

    if not stmts or stmts[0] is None:
        return sql

    # Walk all Table nodes, append the clause as a transformation expression.
    # sqlglot represents `tbl AT(...)` differently per dialect — simplest is
    # to render back to SQL and then text-insert after each table name. That
    # avoids tying us to a dialect-specific AST shape.
    parsed = stmts[0]
    table_names: set[str] = set()
    for t in parsed.find_all(exp.Table):
        table_names.add(_format_table_for_match(t))
    rewritten = sql
    for name in table_names:
        # Insert clause AFTER the table name (and before any alias). Be
        # conservative: only match isolated occurrences.
        pattern = re.compile(
            rf"(\b{re.escape(name)}\b)(?!\s*\.)(?!\s*\()(?!\s*{re.escape(snap.time_travel_clause)})",
            flags=re.IGNORECASE,
        )
        rewritten = pattern.sub(r"\1 " + snap.time_travel_clause, rewritten, count=1)
    return rewritten


def _regex_inject_time_travel(sql: str, clause: str) -> str:
    return re.sub(
        r"\bFROM\s+([A-Za-z0-9_\.\"`]+)",
        lambda m: f"FROM {m.group(1)} {clause}",
        sql, count=1, flags=re.IGNORECASE,
    )


def _format_table_for_match(t) -> str:
    parts = [p for p in (t.catalog, t.db, t.name) if p]
    return ".".join(parts) if parts else t.name
