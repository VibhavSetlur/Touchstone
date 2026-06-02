# Playbooks

A playbook is a pre-canned QA workflow. Operators reach for one when:

- The work is routine enough to encode ("we always do these five things
  before a release").
- The work should be reproducible by anyone on the team, including the AI.
- The work spans multiple Touchstone subsystems (DB + GitHub + knowledge +
  notification) and writing it ad-hoc each time is wasteful.

## Bundled playbooks

### `migration_safety`

For a PR that changes schema. Profiles the table on the sandbox, runs the
PR-impact analyzer, identifies owners, suggests expectations, drafts a
stakeholder ping.

```bash
touchstone playbook run migration_safety --params '{
  "sandbox_connection": "sandbox-pg",
  "table_name": "orders",
  "schema": "public",
  "repo": "acme/warehouse",
  "changes": [
    {"path": "migrations/0042_add_discount.sql",
     "before_sql": "CREATE TABLE orders (id BIGINT, total NUMERIC);",
     "after_sql":  "CREATE TABLE orders (id BIGINT, total NUMERIC, discount_pct NUMERIC);"}
  ],
  "dialect": "postgres"
}'
```

### `dashboard_verify`

Browses to a dashboard, logs in, extracts the rendered table, compares
against a SQL query, reports discrepancies. The original "$10,500.56 in
the DB → $10.5K on the chart" detector.

See [`dashboard-verify.md`](./dashboard-verify.md).

### `anomaly_triage`

"This metric looks off." Pulls recent values, finds who touched the model,
checks knowledge-store notes, drafts a stakeholder note with the right
people to ping.

### `pre_release_check`

Walks a directory of `expectations.yaml` files against a connection. Pass
to block a release tag if any fail.

### `data_handoff`

A new analyst is inheriting a table. Generates a one-page brief: schema,
owners, recent changes, known quirks, gotchas.

## Writing your own

```python
# my_playbook.py
from dataclasses import dataclass
from typing import Any

from touchstone.playbooks.base import Playbook, PlaybookReport, PlaybookStep
from touchstone.security.gateway import Gateway, ToolCallContext


@dataclass(slots=True)
class WeeklyHealthcheck(Playbook):
    name: str = "weekly_healthcheck"
    gateway: Gateway | None = None

    def run(self, *, assistant_id, assistant_session, connection):
        report = PlaybookReport(playbook=self.name)
        out = self.gateway.execute(ToolCallContext(
            assistant_id=assistant_id, assistant_session=assistant_session,
            tool="weekly_healthcheck", connection=connection,
            sql="SELECT COUNT(*) FROM orders WHERE created_at > NOW() - INTERVAL '7 days'",
        ))
        n = int(out.masked.result.rows[0][0])
        report.steps.append(PlaybookStep(
            name="weekly_orders", ok=(n > 1000),
            detail=f"{n:,} orders in the last 7 days",
        ))
        report.summary = f"Health: {n:,} orders this week"
        return report
```

Register it by extending `touchstone.playbooks.REGISTRY` from your operator
config (planned: load custom playbooks from a Python entrypoint group
`touchstone.playbooks`).

## Why playbooks ARE the right abstraction

Versus letting the AI plan from scratch each time:

- **Reproducible** — same input, same steps, same audit trail.
- **Cheap** — encoded routines don't burn LLM tokens to rediscover the
  workflow.
- **Reviewable** — playbooks are code; they can be code-reviewed.

Versus a full BPMN-style workflow engine:

- **Lightweight** — a playbook is one Python class, not an XML graph.
- **AI-callable** — the AI can invoke any playbook by name via MCP.
- **Composable** — playbooks can invoke other playbooks.
