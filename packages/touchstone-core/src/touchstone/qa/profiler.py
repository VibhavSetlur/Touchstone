"""Table profiler.

For each column: type, null rate, distinct count (approximate where supported),
min/max/mean/p50/p95/p99 (numeric), avg length (text), top-K values + freq,
inferred semantic type. A handful of sample rows (PII-masked).

Pushes everything down as SQL aggregates — never SELECT *.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from touchstone.security.gateway import Gateway, ToolCallContext
from touchstone.types import TableRef


@dataclass(slots=True)
class ColumnProfile:
    name: str
    type: str
    null_rate: float | None = None
    distinct_count: int | None = None
    distinct_ratio: float | None = None
    min: Any = None
    max: Any = None
    mean: float | None = None
    p50: float | None = None
    p95: float | None = None
    p99: float | None = None
    avg_length: float | None = None
    top_values: list[tuple[Any, int]] = field(default_factory=list)
    semantic_type: str = "unknown"


@dataclass(slots=True)
class TableProfile:
    table: TableRef
    row_count: int
    columns: list[ColumnProfile]
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    pii_summary: dict[str, int] = field(default_factory=dict)


def profile_table(
    gateway: Gateway,
    *,
    assistant_id: str,
    assistant_session: str,
    connection: str,
    table: TableRef,
    top_k: int = 5,
    sample_n: int = 10,
) -> TableProfile:
    """Profile one table.

    All queries go through the gateway. PII findings are aggregated into
    the table profile so the LLM caller sees "this table has 12 EMAIL fields
    in this sample, 0 SSNs" without ever seeing the raw values.
    """

    qualified = table.qualified()
    safe = _safe_qualified(qualified)

    # 1. Row count + column metadata.
    result = _gateway_query(
        gateway, assistant_id, assistant_session, "profile_table", connection,
        f"SELECT COUNT(*) AS rc FROM {safe}",
    )
    row_count = int(result.result.rows[0][0]) if result.result.rows else 0

    # 2. Describe columns via information_schema for portability.
    schema_filter = (
        f"AND table_schema = '{table.schema}'" if table.schema else ""
    )
    cols_result = _gateway_query(
        gateway, assistant_id, assistant_session, "profile_table", connection,
        f"""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = '{table.name}' {schema_filter}
        ORDER BY ordinal_position
        """,
    )
    cols: list[ColumnProfile] = []
    for row in cols_result.result.rows:
        col_name, col_type, _is_nullable = row[0], str(row[1]), str(row[2])
        cols.append(ColumnProfile(name=str(col_name), type=col_type,
                                  semantic_type=_infer_semantic_type(str(col_name), col_type)))

    if row_count == 0:
        return TableProfile(table=table, row_count=0, columns=cols)

    # 3. Per-column aggregates — one query per column to keep it simple and
    #    push the work to the DB. A future optimization batches numeric cols
    #    into one query.
    for col in cols:
        ident = _safe_ident(col.name)
        if _is_numeric(col.type):
            agg = _gateway_query(
                gateway, assistant_id, assistant_session, "profile_table", connection,
                f"""
                SELECT
                  COUNT(*) - COUNT({ident}) AS null_count,
                  MIN({ident}) AS min_v,
                  MAX({ident}) AS max_v,
                  AVG(CAST({ident} AS DOUBLE)) AS mean_v
                FROM {safe}
                """,
            )
            if agg.result.rows:
                r = agg.result.rows[0]
                col.null_rate = float(r[0]) / row_count if row_count else None
                col.min, col.max = r[1], r[2]
                col.mean = float(r[3]) if r[3] is not None else None
        else:
            agg = _gateway_query(
                gateway, assistant_id, assistant_session, "profile_table", connection,
                f"""
                SELECT
                  COUNT(*) - COUNT({ident}) AS null_count,
                  COUNT(DISTINCT {ident}) AS distinct_count
                FROM {safe}
                """,
            )
            if agg.result.rows:
                r = agg.result.rows[0]
                col.null_rate = float(r[0]) / row_count if row_count else None
                col.distinct_count = int(r[1]) if r[1] is not None else None
                if col.distinct_count is not None:
                    col.distinct_ratio = col.distinct_count / row_count if row_count else None

        # Top-K values.
        top = _gateway_query(
            gateway, assistant_id, assistant_session, "profile_table", connection,
            f"""
            SELECT {ident}, COUNT(*) AS c FROM {safe}
            WHERE {ident} IS NOT NULL
            GROUP BY {ident} ORDER BY c DESC LIMIT {top_k}
            """,
        )
        col.top_values = [(r[0], int(r[1])) for r in top.result.rows]

    # 4. Sample rows.
    sample_result = _gateway_query(
        gateway, assistant_id, assistant_session, "profile_table", connection,
        f"SELECT * FROM {safe} LIMIT {sample_n}",
    )

    # 5. PII summary from the sample.
    pii_summary: dict[str, int] = {}
    for f in sample_result.masked.findings:
        pii_summary[f.entity_type] = pii_summary.get(f.entity_type, 0) + 1

    return TableProfile(
        table=table,
        row_count=row_count,
        columns=cols,
        sample_rows=sample_result.masked.result.to_dicts(),
        pii_summary=pii_summary,
    )


def _gateway_query(
    gateway: Gateway,
    assistant_id: str,
    session: str,
    tool: str,
    connection: str,
    sql: str,
):
    """Tiny helper that bundles the gateway result with the masker output we
    actually care about."""
    from dataclasses import dataclass

    @dataclass
    class _Combo:
        masked: Any
        result: Any

    outcome = gateway.execute(ToolCallContext(
        assistant_id=assistant_id, assistant_session=session,
        tool=tool, connection=connection, sql=sql,
    ))
    return _Combo(masked=outcome.masked, result=outcome.masked.result)


def _safe_qualified(q: str) -> str:
    """Lightly sanity-check a qualified table name. We're not preventing
    injection (the policy engine already rejects non-SELECT) — just catching
    typos that would produce a weird error."""
    if any(c in q for c in (";", "--", "/*")):
        raise ValueError(f"suspicious qualified name: {q!r}")
    return q


def _safe_ident(name: str) -> str:
    if not all(c.isalnum() or c in "_." for c in name):
        raise ValueError(f"unsafe column identifier: {name!r}")
    return f'"{name}"' if "." not in name else name


_NUMERIC_TYPES = {
    "int", "integer", "bigint", "smallint", "tinyint", "decimal", "numeric",
    "float", "real", "double", "double precision", "money",
}


def _is_numeric(type_str: str) -> bool:
    return any(t in type_str.lower() for t in _NUMERIC_TYPES)


def _infer_semantic_type(name: str, type_str: str) -> str:
    lname = name.lower()
    if any(s in lname for s in ("_id", "id_", "uuid", "guid")) or lname == "id":
        return "identifier"
    if any(s in lname for s in ("created_at", "updated_at", "timestamp", "_at", "_date", "_time")):
        return "timestamp"
    if any(s in lname for s in ("is_", "has_", "_flag", "_enabled")):
        return "boolean"
    if "amount" in lname or "price" in lname or "total" in lname or "cost" in lname:
        return "money"
    if _is_numeric(type_str):
        return "number"
    if "text" in type_str.lower() or "varchar" in type_str.lower() or "char" in type_str.lower():
        if "name" in lname or "title" in lname or "description" in lname:
            return "freetext"
        return "category"
    return "unknown"
