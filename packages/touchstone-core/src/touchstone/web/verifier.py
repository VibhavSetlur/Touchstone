"""UI vs DB verifier.

Given:
  - A rendered web page (already navigated by a BrowserSession),
  - A SQL query that should return the "ground truth" data,
  - A mapping of how to align them (key column, value columns, optional
    transform — e.g. format-as-money, round, timezone),

this module extracts the rendered table, runs the SQL through the gateway,
aligns rows, and reports discrepancies.

This is the original "data renders incorrectly on dashboards" use case —
a Looker chart that says $10.5K when the warehouse says $10,500.56 is
caught here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from touchstone.security.gateway import Gateway, ToolCallContext
from touchstone.web.browser import BrowserSession, BrowserStep


@dataclass(slots=True)
class CellDiscrepancy:
    key: Any
    column: str
    rendered: Any
    db: Any
    severity: str  # "exact_mismatch" | "rounded_truncation" | "format_mismatch"


@dataclass(slots=True)
class VerifyReport:
    rendered_rows: int
    db_rows: int
    rows_only_rendered: list[dict[str, Any]] = field(default_factory=list)
    rows_only_db: list[dict[str, Any]] = field(default_factory=list)
    cell_discrepancies: list[CellDiscrepancy] = field(default_factory=list)


def verify_rendered_against_db(
    gateway: Gateway,
    *,
    assistant_id: str,
    assistant_session: str,
    browser: BrowserSession,
    table_selector: str,
    connection: str,
    sql: str,
    key_column: str,
    value_columns: list[str],
    tolerance: float = 0.0,
) -> VerifyReport:
    # 1. Extract rendered table.
    res = browser.execute(BrowserStep(op="extract_table", selector=table_selector))
    if not res.ok or res.table is None:
        return VerifyReport(rendered_rows=0, db_rows=0)
    rendered = res.table

    # 2. Pull DB rows through the gateway (PII-masked already).
    db_out = gateway.execute(ToolCallContext(
        assistant_id=assistant_id, assistant_session=assistant_session,
        tool="verify_rendered_against_db", connection=connection, sql=sql,
    ))
    db_rows = db_out.masked.result.to_dicts()

    # 3. Index both by key.
    rendered_idx = {_norm_key(r.get(key_column)): r for r in rendered}
    db_idx = {_norm_key(r.get(key_column)): r for r in db_rows}

    report = VerifyReport(rendered_rows=len(rendered), db_rows=len(db_rows))
    report.rows_only_rendered = [r for k, r in rendered_idx.items() if k not in db_idx][:50]
    report.rows_only_db = [r for k, r in db_idx.items() if k not in rendered_idx][:50]

    # 4. Compare values for matching rows.
    common = sorted(set(rendered_idx) & set(db_idx))
    for k in common:
        for col in value_columns:
            r_val = rendered_idx[k].get(col)
            d_val = db_idx[k].get(col)
            sev = _compare(r_val, d_val, tolerance)
            if sev is not None:
                report.cell_discrepancies.append(CellDiscrepancy(
                    key=k, column=col, rendered=r_val, db=d_val, severity=sev,
                ))
    return report


def _norm_key(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip()
    return v


_K_SUFFIX = re.compile(r"^([\d,\.]+)\s*([kKmMbB])$")


def _compare(rendered: Any, db: Any, tolerance: float) -> str | None:
    """Return None for "match"; otherwise a severity tag.

    Special case: K/M/B-suffixed rendered values ALWAYS report
    `rounded_truncation` (even when within tolerance) because the rendered
    form has lost precision the caller probably wants to know about. The
    original Looker→$10.5K vs $10,500.56 case lives here.
    """
    if rendered is None and db is None:
        return None
    if rendered is None or db is None:
        return "exact_mismatch"

    rendered_has_suffix = (
        isinstance(rendered, str)
        and _K_SUFFIX.search(rendered.replace(" ", "")) is not None
    )

    # Try numeric comparison.
    r_num = _to_number(rendered)
    d_num = _to_number(db)
    if r_num is not None and d_num is not None:
        if rendered_has_suffix:
            return "rounded_truncation"
        if d_num == 0:
            return None if r_num == 0 else "exact_mismatch"
        rel = abs(r_num - d_num) / abs(d_num)
        if rel <= tolerance:
            return None
        return "exact_mismatch"

    # String compare with whitespace + case fold.
    if str(rendered).strip().lower() == str(db).strip().lower():
        return None
    return "format_mismatch"


def _to_number(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.strip().replace(",", "").replace("$", "").replace("€", "").replace("£", "")
    m = _K_SUFFIX.search(s)
    if m:
        base = float(m.group(1).replace(",", ""))
        scale = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[m.group(2).lower()]
        return base * scale
    try:
        return float(s)
    except ValueError:
        return None
