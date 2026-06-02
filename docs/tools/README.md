# MCP tools reference

The Touchstone MCP server exposes the following tools to AI assistants.

| Tool                          | Reads DB? | Writes DB? | Description                              |
| ----------------------------- | --------- | ---------- | ---------------------------------------- |
| `list_connections`            | no        | no         | List configured connections.             |
| `list_tables`                 | yes       | no         | List tables in a connection.             |
| `describe_table`              | yes       | no         | Column list for a table.                 |
| `query_database`              | yes       | maybe*     | Run a SQL query.                         |
| `profile_table_tool`          | yes       | no         | Stats, distinct counts, sample rows.     |
| `diff_environments_tool`      | yes       | no         | Schema + row-count + value diff.         |
| `check_data_quality_tool`     | yes       | no         | Run YAML expectations.                   |
| `explain_lineage_tool`        | yes       | no         | Column-level lineage.                    |
| `analyze_pr_data_impact_tool` | no        | no         | Parse PR SQL diff — no DB call.          |
| `generate_test_cases_tool`    | yes       | no         | Profile then propose expectations.       |

*Writes via `query_database` are gated: policy must allow, AND consent must
be granted, AND the connection must be marked writable.

## Tool: `query_database`

Run a SQL query against a configured connection.

**Input:**
```json
{
  "connection": "prod-ro",
  "sql": "SELECT order_id, total_amount FROM orders WHERE created_at > NOW() - INTERVAL '1 day' LIMIT 100"
}
```

**Output:**
```json
{
  "columns": [{"name": "order_id", "type": "bigint"}, ...],
  "rows": [...],
  "row_count": 100,
  "truncated": false,
  "pii_findings_summary": {"EMAIL": 0, "PHONE": 0},
  "latency_ms": 142.3
}
```

**Errors:**
- `policy/denied` — connection or SQL violates a rule.
- `consent/required` — consent was requested but not granted in time.
- `connector/timeout` — query exceeded `timeout_seconds`.
- `connector/auth` — credential failure.
- `rate_limited` — assistant exceeded per-minute budget.

## Tool: `profile_table_tool`

Profile a single table. Pushes work to the DB as aggregates.

**Input:**
```json
{"connection": "prod-ro", "table": "orders", "schema": "public", "top_k": 5, "sample_n": 10}
```

**Output:** TableProfile shape — row count, per-column stats, top values, sample rows.

## Tool: `diff_environments_tool`

Compare a table across two connections.

**Input:**
```json
{
  "left_connection": "dev-ro",
  "right_connection": "prod-ro",
  "table": "orders",
  "schema": "public",
  "primary_key": ["order_id"],
  "mode": "schema_and_rowcount"   // or "schema", "full"
}
```

## Tool: `analyze_pr_data_impact_tool`

Static analysis of SQL diffs — no DB calls. Useful in CI before a sandbox
is available.

**Input:**
```json
{
  "changes": [
    {"path": "migrations/0042_add_discount.sql", "before_sql": "...", "after_sql": "..."}
  ],
  "dialect": "snowflake"
}
```

## Tool: `check_data_quality_tool`

Run a YAML-style expectations spec.

**Input:**
```json
{
  "connection": "prod-ro",
  "expectations": {
    "table": "orders",
    "expectations": [
      {"row_count_between": {"min": 1, "max": 1000000}},
      {"column_unique": "order_id"}
    ]
  }
}
```

See [`docs/security/writing-policies.md`](../security/writing-policies.md)
for the expectations DSL.
