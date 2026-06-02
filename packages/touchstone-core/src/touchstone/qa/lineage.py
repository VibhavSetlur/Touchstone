"""Column-level lineage via sqlglot.

Given a target table.column, walks back through views/CTEs to enumerate the
upstream columns that influence it. Optionally enriched with a dbt manifest.

This is a wrapper around `sqlglot.lineage.lineage` that adds:
  - resolution of view definitions via the connector (so we can chase across
    views, not just within a single query);
  - integration with dbt's manifest.json if present;
  - a structured `LineageGraph` that's easy to render to markdown or JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import lineage as sql_lineage

from touchstone.security.gateway import Gateway, ToolCallContext


@dataclass(slots=True)
class LineageNode:
    name: str                         # qualified column: "schema.table.column"
    sources: list[LineageNode] = field(default_factory=list)
    expression: str | None = None     # SQL expression that produced this column
    source_type: str = "column"       # column | derived | aggregate


@dataclass(slots=True)
class LineageGraph:
    target: str
    root: LineageNode
    dialect: str
    notes: list[str] = field(default_factory=list)


def explain_lineage(
    gateway: Gateway,
    *,
    assistant_id: str,
    assistant_session: str,
    connection: str,
    column: str,                # "schema.table.column"
    sql: str | None = None,     # optional: if provided, lineage is computed against this SQL
    dbt_manifest: Path | None = None,
    dialect: str = "",
) -> LineageGraph:
    """Return the lineage of one column.

    Either `sql` is provided directly (the column is computed by that query),
    or we look up the table's view definition. If neither yields a view, the
    column is treated as a base column and the graph is one node deep.
    """

    if sql is None:
        sql = _try_get_view_definition(
            gateway, assistant_id, assistant_session, connection, column,
        )

    notes: list[str] = []

    if sql is None:
        node = LineageNode(name=column)
        return LineageGraph(target=column, root=node, dialect=dialect, notes=["base table column"])

    try:
        col_name = column.split(".")[-1]
        node = sql_lineage.lineage(col_name, sql, dialect=dialect)
        root = _convert(node)
    except sqlglot.errors.SqlglotError as e:
        notes.append(f"sqlglot could not parse the view definition: {e}")
        root = LineageNode(name=column)

    if dbt_manifest is not None:
        _enrich_with_manifest(root, dbt_manifest, notes)

    return LineageGraph(target=column, root=root, dialect=dialect, notes=notes)


def _convert(node: Any) -> LineageNode:
    """Convert a sqlglot lineage.Node tree into our LineageNode."""
    name = node.name or str(node.source) if hasattr(node, "source") else str(node)
    out = LineageNode(name=str(name), expression=str(node.expression) if hasattr(node, "expression") else None)
    for child in getattr(node, "downstream", []) or []:
        out.sources.append(_convert(child))
    return out


def _try_get_view_definition(
    gateway: Gateway, aid: str, sess: str, conn: str, column: str,
) -> str | None:
    parts = column.split(".")
    if len(parts) < 2:
        return None
    schema, table = parts[0], parts[1] if len(parts) == 3 else parts[0]
    table = parts[-2]
    try:
        out = gateway.execute(ToolCallContext(
            assistant_id=aid, assistant_session=sess,
            tool="explain_lineage", connection=conn,
            sql=f"""
                SELECT view_definition FROM information_schema.views
                WHERE table_name = '{table}' AND table_schema = '{schema}'
            """,
        ))
        if out.masked.result.rows:
            return str(out.masked.result.rows[0][0])
    except Exception:
        return None
    return None


def _enrich_with_manifest(root: LineageNode, manifest: Path, notes: list[str]) -> None:
    """Cross-reference each node against a dbt manifest. If a node corresponds
    to a dbt model, append that to the note for the downstream renderers to
    show as a 'dbt source: models/marts/orders.sql'."""
    try:
        with manifest.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        notes.append(f"could not read dbt manifest: {e}")
        return

    nodes = data.get("nodes", {})
    name_to_path: dict[str, str] = {}
    for n in nodes.values():
        if n.get("resource_type") == "model":
            name_to_path[n["name"]] = n.get("original_file_path", "")

    def walk(node: LineageNode) -> None:
        # match by last segment of the qualified name
        leaf = node.name.split(".")[-1]
        if leaf in name_to_path:
            notes.append(f"{node.name}: dbt model at {name_to_path[leaf]}")
        for child in node.sources:
            walk(child)

    walk(root)
