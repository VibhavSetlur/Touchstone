# Snapshot transactions — TOCTOU defense

A long-form QA workflow looks like:

```
T0: profile_table("orders") → schema, row count, sample
T1: AI reasons over result, generates "verify revenue" query
T2: query_database(generated SQL) → results
```

Between T0 and T2 (typically seconds to minutes for an LLM-driven
flow), a parallel dbt run, Airflow job, or ETL pipeline can mutate the
table. The AI's "carefully reasoned" query then runs against a
different reality. Best case: errors. Worst case: silently wrong
numbers in a stakeholder report.

## The fix

`gateway.snapshot()` pins a consistent view of the data for the
duration of a `with:` block. All reads inside the block see the same
state, regardless of what's happening in other sessions.

### Python API

```python
with gateway.snapshot(tenant_id="default", connection="warehouse-ro") as snap:
    profile = profile_table(...)        # at snap.snapshot_ts
    diff    = diff_environments(...)    # also at snap.snapshot_ts
    # ETL running in parallel doesn't affect what we see in here.
```

### MCP API

The AI calls `snapshot_begin("warehouse-ro")` → gets back a snapshot
timestamp. Subsequent calls on `warehouse-ro` from this assistant use
the same snapshot. `snapshot_end("warehouse-ro")` closes it.

```json
{"tool": "snapshot_begin", "args": {"connection": "warehouse-ro"}}
→ {"connection": "warehouse-ro", "snapshot_ts": "2026-06-02T19:00:00Z", "engine": "snowflake"}

{"tool": "query_database", "args": {"connection": "warehouse-ro", "sql": "SELECT * FROM orders LIMIT 10"}}
→ {"rows": [...], "snapshot_ts": "2026-06-02T19:00:00Z", "warnings": ["snapshot pinned at 2026-06-02T19:00:00Z"]}

{"tool": "snapshot_end", "args": {"connection": "warehouse-ro"}}
→ {"status": "closed"}
```

## How it's implemented per engine

| Engine | Mechanism |
|---|---|
| Postgres | `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY` |
| MySQL    | `START TRANSACTION WITH CONSISTENT SNAPSHOT` |
| SQLite   | `BEGIN IMMEDIATE` |
| DuckDB   | `BEGIN TRANSACTION READ ONLY` |
| Snowflake | Capture timestamp; rewrite each query with `AT(TIMESTAMP => '...')` (Time Travel) |
| BigQuery  | Append `FOR SYSTEM_TIME AS OF TIMESTAMP('...')` to each table reference |
| Databricks | Append `TIMESTAMP AS OF '...'` (Delta) |
| Others    | Best-effort timestamp recording in the audit log so reports flag staleness |

For engines that use per-table time-travel decorators (Snowflake, BQ,
Databricks), Touchstone parses each query with `sqlglot` and injects
the clause after every base-table reference. The rewrite happens
**inside the gateway**, after policy approval but before the connector
call — the AI never has to think about time-travel syntax.

For engines with a real transaction (Postgres, MySQL, DuckDB, SQLite),
the connector holds the transaction open for the duration of the
`with:` block. Other concurrent gateway calls on a *different*
connection are unaffected — the lock is per-connector.

## What about engines without snapshot support

If a connector's engine has no snapshot story (some Mongo deployments,
older Redshift without time-travel), the snapshot still tracks the
wall-clock timestamp of first use. Every result inside the block carries
that `snapshot_ts` so the AI knows the data is from then — and downstream
reports can flag "this report spanned X minutes; data may have shifted
mid-flow".

## Cost

- Transaction-based engines: small. Holding a read-only transaction
  open is cheap; the only cost is preventing other writes from
  vacuuming MVCC tombstones until you release.
- Snowflake / BigQuery time-travel: free up to the Time Travel
  retention window (default 1 day; configurable). Past that window,
  the query fails.
- Snowflake specifically: long Time Travel windows on large tables
  cost storage. Keep snapshot durations short.

## When you DON'T want a snapshot

- Long-running interactive sessions where data freshness matters more
  than consistency (e.g. "show me the latest counts").
- Real-time monitoring workflows that should pick up changes mid-flow.

For these, just don't open a snapshot. The default is no snapshot —
opt-in only.
