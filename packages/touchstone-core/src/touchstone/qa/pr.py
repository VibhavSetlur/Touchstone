"""PR-impact analysis.

Given a PR diff containing SQL or dbt changes, predict which tables / columns /
downstream artifacts are affected, and emit a structured report.

Inputs:
  - a list of (file_path, before_sql, after_sql) tuples
  - optionally, a dbt manifest for lineage enrichment
  - optionally, a sandbox connection to actually run before/after diffs against

Outputs:
  - PRImpactReport with tables_added/removed/mutated, columns_retyped,
    downstream_breakage candidates, suggested test cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import diff as sql_diff
from sqlglot import expressions as exp


@dataclass(slots=True)
class FileChange:
    path: str
    before_sql: str
    after_sql: str


@dataclass(slots=True)
class TableChange:
    name: str
    kind: str   # "created" | "dropped" | "mutated"
    details: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ColumnChange:
    table: str
    column: str
    kind: str   # "added" | "removed" | "retyped" | "renamed"
    before: str | None = None
    after: str | None = None


@dataclass(slots=True)
class PRImpactReport:
    files_analyzed: int
    tables: list[TableChange] = field(default_factory=list)
    columns: list[ColumnChange] = field(default_factory=list)
    downstream_risks: list[str] = field(default_factory=list)
    suggested_tests: list[str] = field(default_factory=list)
    parse_failures: list[str] = field(default_factory=list)


def analyze_pr_data_impact(
    *,
    changes: list[FileChange],
    dialect: str = "",
    dbt_manifest: Path | None = None,
) -> PRImpactReport:
    report = PRImpactReport(files_analyzed=len(changes))

    for change in changes:
        try:
            before = sqlglot.parse(change.before_sql, read=dialect) if change.before_sql else []
            after = sqlglot.parse(change.after_sql, read=dialect) if change.after_sql else []
        except sqlglot.errors.ParseError as e:
            report.parse_failures.append(f"{change.path}: {e}")
            continue

        _analyze_table_changes(before, after, report)
        _analyze_column_changes(before, after, report)
        _analyze_query_changes(before, after, change.path, report)

    if dbt_manifest is not None:
        _enrich_downstream_with_manifest(report, dbt_manifest)

    _suggest_tests(report)
    return report


def _analyze_table_changes(
    before: list, after: list, report: PRImpactReport,
) -> None:
    before_tables = _extract_created_tables(before)
    after_tables = _extract_created_tables(after)

    for t in after_tables - before_tables:
        report.tables.append(TableChange(name=t, kind="created"))
    for t in before_tables - after_tables:
        report.tables.append(TableChange(name=t, kind="dropped"))


def _extract_created_tables(stmts: list) -> set[str]:
    out: set[str] = set()
    for stmt in stmts:
        if stmt is None:
            continue
        for create in stmt.find_all(exp.Create):
            if create.kind and create.kind.upper() in ("TABLE", "VIEW", "MATERIALIZED VIEW"):
                target = create.this
                if isinstance(target, exp.Schema):
                    target = target.this
                if isinstance(target, exp.Table):
                    out.add(str(target.name))
    return out


def _analyze_column_changes(
    before: list, after: list, report: PRImpactReport,
) -> None:
    before_cols = _extract_columns(before)
    after_cols = _extract_columns(after)

    for (table, col), col_type in after_cols.items():
        prev = before_cols.get((table, col))
        if prev is None:
            report.columns.append(ColumnChange(table=table, column=col, kind="added", after=col_type))
        elif prev != col_type:
            report.columns.append(ColumnChange(
                table=table, column=col, kind="retyped", before=prev, after=col_type,
            ))

    for (table, col), col_type in before_cols.items():
        if (table, col) not in after_cols:
            report.columns.append(ColumnChange(
                table=table, column=col, kind="removed", before=col_type,
            ))


def _extract_columns(stmts: list) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for stmt in stmts:
        if stmt is None:
            continue
        for create in stmt.find_all(exp.Create):
            if not create.kind or create.kind.upper() != "TABLE":
                continue
            target = create.this
            if isinstance(target, exp.Schema):
                table_name = str(target.this.name) if isinstance(target.this, exp.Table) else ""
                for col_def in target.expressions:
                    if isinstance(col_def, exp.ColumnDef):
                        out[(table_name, str(col_def.name))] = str(col_def.kind) if col_def.kind else "unknown"
    return out


def _analyze_query_changes(
    before: list, after: list, path: str, report: PRImpactReport,
) -> None:
    """For SQL files that contain queries (not just DDL), compute an AST diff
    and flag risky changes: removed columns, changed WHERE clauses, etc."""
    if not before or not after or before[0] is None or after[0] is None:
        return

    # sqlglot.diff returns edit operations; we summarize.
    try:
        edits = sql_diff(before[0], after[0])
    except Exception:
        return

    significant = sum(1 for e in edits if type(e).__name__ in ("Insert", "Remove", "Move", "Update"))
    if significant > 0:
        report.downstream_risks.append(
            f"{path}: {significant} significant AST change(s) — review carefully."
        )


def _enrich_downstream_with_manifest(report: PRImpactReport, manifest: Path) -> None:
    """For each touched table, look up its dbt model and enumerate downstream
    consumers via the manifest's `child_map`."""
    import json
    try:
        with manifest.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    children = data.get("child_map", {})
    name_to_unique = {n["name"]: uid for uid, n in data.get("nodes", {}).items()}
    for t in report.tables:
        uid = name_to_unique.get(t.name)
        if uid:
            for downstream in children.get(uid, []):
                report.downstream_risks.append(f"{t.name} → {downstream}")


def _suggest_tests(report: PRImpactReport) -> None:
    """Propose validator expectations for the change shape."""
    for c in report.columns:
        if c.kind == "added":
            report.suggested_tests.append(f"column_not_null: {c.table}.{c.column}")
        if c.kind == "retyped":
            report.suggested_tests.append(
                f"column_values_castable: {c.table}.{c.column} → {c.after}"
            )
    for t in report.tables:
        if t.kind == "created":
            report.suggested_tests.append(f"row_count_between: {t.name} (min: 1)")
        if t.kind == "dropped":
            report.suggested_tests.append(
                f"verify no remaining references to {t.name} before merge"
            )
