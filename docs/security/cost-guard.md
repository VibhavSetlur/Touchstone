# Cost guardrails

"Read-only" does not mean "safe". A SELECT can scan billions of rows.
A cross-join can produce a Cartesian explosion. An LLM in a retry loop
can spawn 50 parallel deep scans. The cost guard is what stops these.

## Three layers

### 1. Static SQL guard

Runs first, before anything touches the database. Implemented via
`sqlglot` AST inspection.

- **Cross-joins are refused.** Both explicit `CROSS JOIN` and an
  implicit `JOIN` without `ON`/`USING`. Operators who genuinely need
  cross-joins disable per-connection: `cost.refuse_cross_join = false`.
- **`SELECT *` on tagged-`large` tables without `LIMIT` is refused.**
  The `large_tables` config lists tables that should never be fully
  scanned interactively.
- **Auto-injected `LIMIT`.** If a query is an unbounded non-aggregating
  SELECT, Touchstone appends `LIMIT 10000` (configurable) and reports
  the rewrite in the response's `warnings` array. Aggregating queries
  (GROUP BY, top-level COUNT/SUM/etc., DISTINCT) are left alone because
  a LIMIT would change the semantic answer.

### 2. EXPLAIN preflight

For engines where `EXPLAIN` returns a useful cost estimate, we run it
before the real query. Per-engine adapters:

| Engine | Mechanism | Estimate |
|---|---|---|
| Postgres / Redshift | `EXPLAIN (FORMAT JSON)` | rows + cost |
| MySQL | `EXPLAIN FORMAT=JSON` | rows + cost |
| BigQuery | `QueryJobConfig(dry_run=True)` | **exact bytes processed** |
| Snowflake | `EXPLAIN USING TABULAR` | plan-only |
| DuckDB | `EXPLAIN` | plan-only |

If the estimate exceeds `max_estimated_rows` (default 100M) or
`max_estimated_bytes` (default 10 GB), the query is refused with
`CostGuardError`. Engines without a useful EXPLAIN fall back to the
static guard + the connector's `row_cap` + the per-assistant timeout.

For BigQuery specifically, the dry-run is **free** and exact — no
billing for the estimate. This is the strongest cost guard we can offer
on any engine.

### 3. Per-assistant concurrency cap

Each tool call acquires a slot in a per-assistant in-flight counter
(default cap: 4). A panicked AI cannot spawn 50 parallel deep scans.
The cap is shared across all connections for the same assistant — the
intent is "an AI in a retry loop hits the wall fast".

## Config

```toml
[cost]
max_estimated_rows = 100_000_000          # reject queries above this rows estimate
max_estimated_bytes = 10_737_418_240      # reject above 10 GB scan estimate
refuse_cross_join = true
require_limit_on_large = true
auto_inject_limit = 10000                 # 0 to disable
concurrent_cap_per_assistant = 4
large_tables = ["events", "raw_logs", "clickstream"]  # by name
```

## When the guard fires

The AI gets a structured error:

```json
{
  "error": "cost_guard/refused",
  "message": "EXPLAIN estimates 5,400,000,000 rows scanned (limit 100,000,000). Refusing."
}
```

This is an *informative* refusal — the AI can reason about it, e.g.
"I should add a WHERE clause" or "I should ask the operator about
the threshold". It's not a silent kill.

When the static guard rewrites the SQL (auto-LIMIT), the warning
appears in the result:

```json
{
  "rows": [...],
  "warnings": ["auto-injected LIMIT 10000 (query had no LIMIT and was not aggregating)"],
  ...
}
```

## Why concurrency caps matter

Without them, a single assistant attempting to "explore" a large
warehouse can issue dozens of long-running scans in parallel. Each one
holds a connection, each one consumes Snowflake credits. Cost spikes
faster than humans can react.

With the cap, the assistant queues at the gate. It can still do the
work, just serially. The audit log captures every queued/refused call,
so cost spikes are visible in retrospect.

## What this doesn't catch

- A query that *should* be fast but isn't (missing index, stale stats,
  bad query plan). EXPLAIN catches the estimated case, not surprises.
- Workload from outside Touchstone. If a human is also running queries
  against the same warehouse, the cost guard sees only the AI's slice.
- Cost in absolute dollars. We estimate bytes/rows; dollars depend on
  your provider's billing. Pair with the provider's own resource monitor.
