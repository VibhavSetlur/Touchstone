"""Declarative data-quality expectations.

Operators (and AI assistants) write expectations in YAML; the validator
translates them to SQL aggregates and runs them through the gateway.

Inspired by Great Expectations and Soda Core, but cross-engine and minimal —
the goal is "obvious to read, easy to add to a PR", not "exhaustive."

Example:
    table: orders
    expectations:
      - row_count_between: {min: 1000, max: 10_000_000}
      - column_not_null: order_id
      - column_unique: order_id
      - column_values_in_set: {column: currency, set: [USD, EUR, GBP, JPY]}
      - column_freshness_seconds: {column: created_at, max: 3600}
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from touchstone.security.gateway import Gateway, ToolCallContext


@dataclass(slots=True)
class ExpectationResult:
    name: str
    passed: bool
    observed: Any = None
    expected: Any = None
    message: str = ""


@dataclass(slots=True)
class ValidationReport:
    table: str
    connection: str
    results: list[ExpectationResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)


def check_data_quality(
    gateway: Gateway,
    *,
    assistant_id: str,
    assistant_session: str,
    connection: str,
    expectations_path: str | Path | None = None,
    expectations: dict[str, Any] | None = None,
) -> ValidationReport:
    spec = expectations or _load(Path(expectations_path)) if expectations_path else expectations
    if not spec:
        raise ValueError("provide either expectations dict or expectations_path")
    table = spec["table"]
    safe = _safe_ident(table) if "." not in table else table
    results: list[ExpectationResult] = []
    for exp in spec.get("expectations", []):
        results.append(_run_one(gateway, assistant_id, assistant_session, connection, safe, exp))
    return ValidationReport(table=table, connection=connection, results=results)


def _run_one(
    gateway: Gateway, aid: str, sess: str, conn: str, safe: str, exp: dict[str, Any],
) -> ExpectationResult:
    name, args = next(iter(exp.items()))
    handler = _EXPECTATIONS.get(name)
    if handler is None:
        return ExpectationResult(name=name, passed=False,
                                 message=f"unknown expectation: {name!r}")
    return handler(gateway, aid, sess, conn, safe, args)


# -- expectation handlers --------------------------------------------------

def _exp_row_count_between(gw: Gateway, aid: str, sess: str, conn: str,
                           safe: str, args: dict[str, int]) -> ExpectationResult:
    out = gw.execute(ToolCallContext(
        assistant_id=aid, assistant_session=sess, tool="check_data_quality",
        connection=conn, sql=f"SELECT COUNT(*) FROM {safe}"
    ))
    n = int(out.masked.result.rows[0][0])
    mn, mx = args.get("min", 0), args.get("max", float("inf"))
    return ExpectationResult(
        name="row_count_between", passed=(mn <= n <= mx),
        observed=n, expected={"min": mn, "max": mx},
    )


def _exp_column_not_null(gw: Gateway, aid: str, sess: str, conn: str,
                         safe: str, args: str) -> ExpectationResult:
    col = _safe_ident(args)
    out = gw.execute(ToolCallContext(
        assistant_id=aid, assistant_session=sess, tool="check_data_quality",
        connection=conn, sql=f"SELECT COUNT(*) FROM {safe} WHERE {col} IS NULL"
    ))
    nulls = int(out.masked.result.rows[0][0])
    return ExpectationResult(
        name="column_not_null", passed=(nulls == 0),
        observed=nulls, expected=0,
        message=f"{nulls} null(s) in {args!r}" if nulls else "",
    )


def _exp_column_unique(gw: Gateway, aid: str, sess: str, conn: str,
                       safe: str, args: str) -> ExpectationResult:
    col = _safe_ident(args)
    out = gw.execute(ToolCallContext(
        assistant_id=aid, assistant_session=sess, tool="check_data_quality",
        connection=conn, sql=f"""
            SELECT COUNT(*) FROM (
              SELECT {col} FROM {safe}
              GROUP BY {col} HAVING COUNT(*) > 1
            ) t
        """,
    ))
    dups = int(out.masked.result.rows[0][0])
    return ExpectationResult(
        name="column_unique", passed=(dups == 0),
        observed=dups, expected=0,
    )


def _exp_column_values_in_set(gw: Gateway, aid: str, sess: str, conn: str,
                              safe: str, args: dict[str, Any]) -> ExpectationResult:
    col = _safe_ident(args["column"])
    allowed = args["set"]
    placeholders = ", ".join(f"'{_sql_escape(v)}'" for v in allowed)
    out = gw.execute(ToolCallContext(
        assistant_id=aid, assistant_session=sess, tool="check_data_quality",
        connection=conn, sql=(
            f"SELECT COUNT(*) FROM {safe} "
            f"WHERE {col} IS NOT NULL AND {col} NOT IN ({placeholders})"
        ),
    ))
    bad = int(out.masked.result.rows[0][0])
    return ExpectationResult(
        name="column_values_in_set", passed=(bad == 0),
        observed=bad, expected=0,
        message=f"{bad} value(s) outside {allowed!r}" if bad else "",
    )


def _exp_column_freshness_seconds(gw: Gateway, aid: str, sess: str, conn: str,
                                  safe: str, args: dict[str, Any]) -> ExpectationResult:
    col = _safe_ident(args["column"])
    max_age = int(args["max"])
    out = gw.execute(ToolCallContext(
        assistant_id=aid, assistant_session=sess, tool="check_data_quality",
        connection=conn, sql=(
            f"SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MAX({col}))) FROM {safe}"
        ),
    ))
    age = float(out.masked.result.rows[0][0] or 0)
    return ExpectationResult(
        name="column_freshness_seconds", passed=(age <= max_age),
        observed=age, expected={"max": max_age},
    )


_EXPECTATIONS = {
    "row_count_between": _exp_row_count_between,
    "column_not_null": _exp_column_not_null,
    "column_unique": _exp_column_unique,
    "column_values_in_set": _exp_column_values_in_set,
    "column_freshness_seconds": _exp_column_freshness_seconds,
}


def _load(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


def _safe_ident(name: str) -> str:
    if not all(c.isalnum() or c == "_" for c in name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return f'"{name}"'


def _sql_escape(v: Any) -> str:
    return str(v).replace("'", "''")
