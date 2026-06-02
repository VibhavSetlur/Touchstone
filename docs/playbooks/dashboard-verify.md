# Playbook: `dashboard_verify`

Drives a browser to a dashboard, logs in, extracts a rendered table, runs a
SQL "ground truth" query, and reports any cell-level discrepancies. Catches
the classic "the warehouse says $10,500.56 but the chart says $10.5K" bug.

## When to run it

- After a PR changes a dashboard's chart logic or filter.
- Weekly, on dashboards that drive a financial or regulatory metric.
- After a DB migration that re-typed a column the dashboard reads.
- When a stakeholder reports "the numbers look weird."

## Setup

Add the dashboard's origin to `web.allowed_origins` and register the login
credential:

```toml
[web]
allowed_origins = ["https://looker.acme.com"]

[web.credentials.looker_admin]
username = "env://LOOKER_USER"
password = "env://LOOKER_PASSWORD"
```

## Inputs

| Param            | Description                                                |
| ---------------- | ---------------------------------------------------------- |
| `dashboard_url`  | URL to navigate to.                                        |
| `credential`     | Credential reference name (NOT a raw login).               |
| `login_url`      | Optional explicit login URL if different from dashboard.   |
| `table_selector` | CSS selector for the rendered table.                       |
| `connection`     | Touchstone connection name to query for ground truth.      |
| `sql`            | Ground-truth SQL — should return one row per `key_column`. |
| `key_column`     | Column to align rendered & DB rows on.                     |
| `value_columns`  | Columns to compare cell-by-cell.                           |
| `tolerance`      | Float — relative tolerance for numeric compare. 0 = exact. |

## Example

```bash
touchstone playbook run dashboard_verify --params '{
  "dashboard_url": "https://looker.acme.com/dashboards/finance/daily-rev",
  "credential": "looker_admin",
  "table_selector": "table.results-table",
  "connection": "warehouse-ro",
  "sql": "SELECT day, total_rev_usd FROM marts.daily_revenue WHERE day > CURRENT_DATE - 30 ORDER BY day",
  "key_column": "day",
  "value_columns": ["total_rev_usd"],
  "tolerance": 0.01
}'
```

## Discrepancy classification

The verifier classifies each mismatch:

- **`exact_mismatch`** — values differ beyond tolerance.
- **`rounded_truncation`** — rendered value uses `K`/`M`/`B` suffix; DB
  value is the unrounded number. Compare normalized.
- **`format_mismatch`** — strings differ even after whitespace + case
  folding (e.g. dates rendered differently).

Use this in your release criteria: any `exact_mismatch` = block, any
`rounded_truncation` = log + monitor, any `format_mismatch` = file ticket.

## Output

A `PlaybookReport` whose `.steps` contain:

1. `login` — ok/fail
2. `navigate` — ok/fail + final URL
3. `compare` — counts + per-cell discrepancies + suggested follow-ups

The report's `to_markdown()` renders nicely in a PR comment or Slack
message.

## Gotchas

- **Virtualized tables**: React/Vue grids that render rows lazily. Pass a
  more specific selector or paginate via additional browser steps before
  the playbook runs.
- **Multiple data sources**: if the dashboard joins multiple warehouses,
  your ground-truth SQL needs to mimic that. Otherwise discrepancies are
  expected.
- **Timezones**: dashboards default to the viewer's timezone. Make sure
  your SQL converts to the same timezone explicitly.
