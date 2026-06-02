# Touchstone

> The QA copilot's copilot — safe, audited, multi-system access for AI coding assistants.

Touchstone is an open-source toolkit that gives AI coding assistants (GitHub
Copilot, Claude, Cursor, Continue, and any [MCP](https://modelcontextprotocol.io/)-aware
client) read-mostly, policy-gated access to **everything a QA analyst touches**
in a workday:

- Databases (Snowflake, Postgres, MySQL, BigQuery, Mongo, Databricks, +6 more).
- Flat files (CSV, Excel, Parquet, JSON, NDJSON) — same SQL surface.
- Web dashboards behind a login (Looker, Retool, Tableau, internal apps) via
  headless browser automation — without the AI ever seeing the password.
- GitHub: PR diffs, blame, CODEOWNERS, recent activity, who-changed-what.
- A persistent knowledge store: notes, owners, tasks, decisions, PR history.
- Notification channels (Slack, Teams, email) by name — never by webhook URL.
- A pluggable LLM provider (Anthropic / OpenAI / Azure / Bedrock / Vertex /
  self-hosted) for Touchstone's own internal reasoning.
- Pre-canned playbooks for routine QA work (migration safety, dashboard
  verify, anomaly triage, pre-release sign-off, data handoff).

All under one structural rule: **the AI never sees credentials**. See
[`docs/security/ai-credential-blindness.md`](docs/security/ai-credential-blindness.md).

```
┌─────────────────────────────────────────────────────────────────┐
│  AI Assistant (Copilot / Claude / Cursor / Continue / ...)      │
└────────────────────────────┬────────────────────────────────────┘
                             │  Model Context Protocol
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Touchstone MCP Server                        │
│   query · profile · diff · lineage · pr-impact · test-gen       │
│   browse · verify-dashboard · run-playbook · notify             │
│   notes · tasks · decisions · who-owns · who-changed            │
├─────────────────────────────────────────────────────────────────┤
│   Policy · PII Masking · Audit · Consent · Rate-limit           │
│   Credential resolver (env / keyring / vault / cloud KMS)       │
├─────────────────────────────────────────────────────────────────┤
│ Snowflake | Postgres | MySQL | BigQuery | Mongo | Databricks    │
│ Redshift  | Trino    | ClickHouse | DuckDB | SQLite | Files     │
│ Web (Playwright, allowlisted origins, password-blind)           │
│ GitHub (read-only intel) · Knowledge (SQLite) · LLM adapter     │
│ Notifications (Slack / Teams / Email by channel name)           │
└─────────────────────────────────────────────────────────────────┘
                             ▲
                  ┌──────────┼──────────┐
                  │          │          │
            [GitHub App] [CLI]  [Triage Web UI]
```

## Why Touchstone

Modern AI coding assistants see only static code. The expensive class of
QA bugs lives outside the code — in the data, the dashboards, the migration
running against real volumes, the PR that silently broke a downstream
model, the dashboard that rounds $10,500.56 down to $10.5K.

Catching these used to mean a human cloning the repo, opening a DB IDE,
logging into Looker, reading three Slack threads, and posting a PR comment.
Touchstone lets the AI do all of that — *safely*, with auditable guardrails,
against the systems your team already uses, **without ever handing the AI
any credentials**.

## What's in the box

| Component                    | What it does                                                                | Status |
| ---------------------------- | --------------------------------------------------------------------------- | ------ |
| `touchstone-core`            | Connectors, security primitives, QA capabilities, web automation, knowledge store, LLM adapter, playbooks, notifications. | Beta |
| `touchstone-mcp`             | MCP server: ~30 tools across DB / files / web / knowledge / GitHub / playbooks / notify. | Beta |
| `touchstone-cli`             | `touchstone` CLI mirroring every capability for local & CI use.            | Beta |
| `touchstone-github`          | Probot GitHub App for PR comments + check runs.                            | Alpha |
| `touchstone-ui`              | Next.js triage UI: audit explorer, consent queue, lineage view.            | Alpha |

## Install

Touchstone is not yet on PyPI — install from this repo:

```bash
# One-line installer (picks uv → pipx → pip automatically):
curl -fsSL https://raw.githubusercontent.com/VibhavSetlur/Touchstone/main/install.sh | bash

# Or clone + Makefile:
git clone https://github.com/VibhavSetlur/Touchstone.git
cd Touchstone
make install        # quickstart bundle: Postgres + DuckDB + web automation
# Or: make install-all  for every connector + LLM provider

# Or directly with pip:
pip install -e packages/touchstone-core[quickstart]
pip install -e packages/touchstone-mcp
pip install -e packages/touchstone-cli
```

Verify:

```bash
touchstone --version
touchstone init                # interactive wizard
touchstone doctor              # diagnose your setup
```

A real 5-minute end-to-end path lives in [`GETTING-STARTED.md`](GETTING-STARTED.md).

## Wire into your AI assistant

| Assistant | Doc | Config file |
|---|---|---|
| **Claude Code** | [`docs/integrations/claude-code.md`](docs/integrations/claude-code.md) | `~/.claude/mcp_servers.json` |
| **Cursor** | [`docs/integrations/cursor.md`](docs/integrations/cursor.md) | `~/.cursor/mcp.json` |
| **Continue** | [`docs/integrations/continue.md`](docs/integrations/continue.md) | `~/.continue/config.yaml` |
| **GitHub Copilot** | [`docs/integrations/github-copilot.md`](docs/integrations/github-copilot.md) | VS Code `settings.json` |

The Claude Code config looks like:

```json
{
  "mcpServers": {
    "touchstone": {
      "command": "touchstone-mcp",
      "args": ["--config", "/Users/YOU/.touchstone/config.toml"],
      "env": {"TOUCHSTONE_ASSISTANT_ID": "claude-code@me@acme"}
    }
  }
}
```

Now ask: *"Profile the `customers` table on `local-pg`. Then log into the
staging Looker dashboard and verify the daily-revenue chart matches the
warehouse. If anything looks off, ping the data team."*

The AI will:

1. Call `profile_table_tool` (DB read, PII auto-masked).
2. Call `browse` + `verify_dashboard` (web automation; password resolved
   by Touchstone, never seen by the AI).
3. Call `notify("data-team-alerts", ...)` (Slack channel resolved by name,
   webhook URL never seen by the AI).
4. Every step gets a hash-chained audit record.

### As a CLI

```bash
# Core QA
touchstone profile warehouse-ro orders
touchstone diff   dev-ro warehouse-ro orders --primary-key order_id
touchstone check  warehouse-ro expectations/orders.yaml

# PR review
touchstone pr --repo acme/warehouse --pr 1423

# Who?
touchstone who acme/warehouse models/marts/orders.sql

# Knowledge
touchstone knowledge note add "table:orders" "TZ quirk on Friday loads — see PR #1421"
touchstone knowledge sync codeowners --repo acme/warehouse
touchstone knowledge sync prs        --repo acme/warehouse --limit 200

# Playbooks
touchstone playbook list
touchstone playbook run dashboard_verify --params '{...}'

# Browser
touchstone browse session.json

# Notify
touchstone notify data-team-alerts "weekly QA pass — all green"

# Audit
touchstone audit verify
touchstone audit tail -n 50
```

## Supported sources

| Source            | Driver / mechanism             | Reads      |
| ----------------- | ------------------------------ | ---------- |
| PostgreSQL        | `psycopg`                       | ✅         |
| MySQL / MariaDB   | `pymysql`                       | ✅         |
| Snowflake         | `snowflake-connector-python`    | ✅         |
| BigQuery          | `google-cloud-bigquery`         | ✅         |
| Databricks SQL    | `databricks-sql-connector`      | ✅         |
| Redshift          | `redshift-connector`            | ✅         |
| MongoDB           | `pymongo`                       | ✅         |
| Trino / Presto    | `trino`                         | ✅         |
| ClickHouse        | `clickhouse-connect`            | ✅         |
| DuckDB            | `duckdb`                        | ✅         |
| SQLite            | stdlib                          | ✅         |
| **Flat files**    | DuckDB (CSV / Excel / Parquet / JSON / NDJSON) | ✅ |
| **Web dashboards** | Playwright, allowlisted, password-blind | ✅ |

Adding a connector is ~150 lines — see
[`docs/connectors/adding-a-connector.md`](docs/connectors/adding-a-connector.md).

## Security model in one screen

1. **The AI sees names, not secrets.** Connection names, channel names,
   credential references — never plaintext. Enforced by config loader,
   MCP schemas, browser session, and connector layer.
   See [`docs/security/ai-credential-blindness.md`](docs/security/ai-credential-blindness.md).
2. **MFA-aware browser automation.** `touchstone session bootstrap` runs
   a headed login once per credential; Touchstone captures the session,
   AES-GCM-256 encrypts it, replays it headlessly. Mid-session expiry
   is auto-detected and refused. The AI never sees cookies or keys.
   See [`docs/integrations/mfa-bootstrap.md`](docs/integrations/mfa-bootstrap.md).
3. **Operator-declared sensitivity catalog** + **local NER detector**
   catches what regex/Presidio misses in unstructured fields. The
   `never_leaves` tier returns only `<TEXT len=437>` — the LLM knows
   data exists but never sees a byte. See
   [`docs/security/sensitivity-catalog.md`](docs/security/sensitivity-catalog.md).
4. **Cost guard with EXPLAIN preflight.** Static SQL guard refuses
   cross-joins and auto-injects LIMIT; EXPLAIN preflight (including
   BigQuery dry-run for exact bytes) rejects queries above row/byte
   thresholds; per-assistant concurrency cap (default 4) stops a
   panicked AI from spawning a fleet of deep scans.
   See [`docs/security/cost-guard.md`](docs/security/cost-guard.md).
5. **Per-tenant isolation** keyed by `(tenant_id, connection_name)`.
   Connector instances never cross tenants; manifest gates which
   connections each tenant can address; audit records are tenant-tagged.
   See [`docs/security/multi-tenant.md`](docs/security/multi-tenant.md).
6. **Snapshot transactions** for TOCTOU defense. `gateway.snapshot()`
   pins a consistent view (Postgres REPEATABLE READ, Snowflake/BQ/
   Databricks Time Travel) so multi-step QA workflows see the same data
   start-to-finish. See [`docs/security/snapshot-transactions.md`](docs/security/snapshot-transactions.md).
7. **Append-only hash-chained audit log** with size-based rotation.
   `touchstone audit verify-rotated` walks across rotated files
   end-to-end. Sinks to file / S3 / Splunk HEC / Datadog / OTLP.
8. **Knowledge store refuses PII dumps at write** — defense against
   future vector-search poisoning.
9. **The trust boundary is structural, not conventional.** QA
   capabilities cannot import connectors directly; AST-scan tests
   enforce it.
10. **Local-first deployment.** Runs entirely in your VPC. No phone-home.

Full threat model: [`SECURITY.md`](SECURITY.md). Hardening review against
the audit findings:
[`docs/security/architectural-vulnerabilities.md`](docs/security/architectural-vulnerabilities.md).

## Comparisons

| If you need...                                | Use...                                       | Touchstone fits where...                                       |
| --------------------------------------------- | -------------------------------------------- | --------------------------------------------------------------- |
| Declarative pipeline DQ                       | Great Expectations / Soda / dbt tests        | You also want AI-driven ad-hoc QA + dashboard verification     |
| Diff-style data testing in dbt                | Datafold OSS (archived) / Recce              | You want multi-DB + MCP + GitHub + web automation              |
| Data observability platform                   | Monte Carlo / Acceldata / Bigeye (paid)      | You want OSS, on-prem, AI-native, PR-time                       |
| Generic single-DB MCP servers                 | `mcp-server-postgres`, etc.                  | You need policy + PII + audit + multi-DB + web + knowledge     |
| Browser automation for QA                     | Selenium / raw Playwright                    | You want password-blind, allowlisted, audited browser access   |

## Project status

Beta for core / MCP / CLI. Alpha for the GitHub App and Triage UI. APIs may
shift before 1.0. Contributions welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).
