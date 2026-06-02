# GitHub PR bot

Install Touchstone as a GitHub App. When a PR touches `*.sql`,
`models/**/*.sql`, `migrations/**`, or schema files, the bot posts an
auto-generated data-impact report as a PR comment + check run.

## What it catches

- **Tables dropped or created** in a migration.
- **Columns added / removed / retyped** between PR base and head.
- **Downstream dbt models** that reference an about-to-change column
  (when `manifest.json` is provided).
- **SQL parse failures** in changed files.

## What it does NOT do (by design)

- Run actual queries against prod. The bot only reads PR file contents and
  parses them. To get value-level diffs, configure a sandbox connection and
  use the CLI in CI.
- Approve sensitive operations. Consent flow is a roadmap item.

## Installation

1. Create a GitHub App at https://github.com/settings/apps/new.
2. Permissions: Repository:Pull requests (Read & write), Checks (Read & write),
   Contents (Read-only), Metadata (Read-only).
3. Subscribe to events: `Pull request`.
4. Deploy the bot via Docker (see `docker/touchstone-github.Dockerfile`).
5. Install the app to your org.

## Configuration

Drop a `.touchstone.yml` in the repo root to tweak per-repo behavior:

```yaml
# .touchstone.yml
dialect: snowflake
dbt_manifest: target/manifest.json
include_paths:
  - "models/**/*.sql"
  - "migrations/**/*.sql"
exclude_paths:
  - "models/scratch/**"
checks:
  fail_on_drop: true
  fail_on_retype: false
```

## Example PR comment

```
### Touchstone — data-impact report

**3 file(s) analyzed — 1 table change(s) · 4 column change(s) · 2 downstream risk(s).**

#### Tables
- ✚ created: `orders_v2`

#### Columns
| Table     | Column         | Change   | Before | After   |
|-----------|----------------|----------|--------|---------|
| `orders`  | `discount_pct` | added    | —      | NUMERIC |
| `orders`  | `currency`     | retyped  | CHAR(3)| VARCHAR |
| `orders`  | `legacy_total` | removed  | NUMERIC| —       |

#### ⚠ Downstream risks
- orders → models/marts/orders_enriched.sql
- orders → models/marts/customer_ltv.sql

<details><summary>Suggested tests to add</summary>

```yaml
- column_not_null: orders.discount_pct
- column_values_castable: orders.currency → VARCHAR
- verify no remaining references to orders.legacy_total before merge
```
</details>
```
