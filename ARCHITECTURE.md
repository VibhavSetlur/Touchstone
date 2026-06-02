# Touchstone — Architecture

This document describes Touchstone's internals: the layering, the data flow, the policy model, and the trade-offs taken. Read [`README.md`](README.md) first for the elevator pitch.

## Design goals (in priority order)

1. **Safe by default.** A misconfigured Touchstone should be useless to an attacker, not catastrophic to a company.
2. **Multi-stack.** A QA team that uses Snowflake + dbt + GitHub should be served as well as one on Postgres + Airflow + GitLab.
3. **AI-assistant agnostic.** Anything that speaks MCP wins. We do not bet on one assistant vendor.
4. **Operate inside the firewall.** No data ever has to leave the customer environment. No vendor-controlled control plane is required.
5. **Cheap to extend.** Adding a connector, a PII detector, or a policy rule should be a single short file.

## Layered architecture

```
                ┌─────────────────────────────────────────────────────────────┐
   Surfaces     │  MCP Server   |   CLI   |   GitHub App   |   Triage UI      │
                └────────────┬──────────────┬────────────────┬────────────────┘
                             │              │                │
                ┌────────────▼──────────────▼────────────────▼────────────────┐
   QA           │  profiler · differ · validator · test-gen · lineage · pr   │
   Capabilities └────────────────────────────┬────────────────────────────────┘
                                             │
                ┌────────────────────────────▼────────────────────────────────┐
   Trust        │  Policy Engine · PII Detector · Masker · Consent Gate ·    │
   Boundary     │  Audit Logger · Rate Limiter · Result Truncator             │
                └────────────────────────────┬────────────────────────────────┘
                                             │
                ┌────────────────────────────▼────────────────────────────────┐
   I/O          │  Connector Registry → {Postgres, MySQL, Snowflake, BigQuery,│
                │  Mongo, Databricks, Trino, ClickHouse, DuckDB, SQLite, ...} │
                └─────────────────────────────────────────────────────────────┘
```

Each upper layer talks **only** to the layer immediately below. Surfaces never reach into connectors directly — they go through QA capabilities, which go through the trust boundary, which fans out to connectors. This is what makes "PII can't reach the LLM" a structural guarantee rather than a code-review convention.

## Repository layout

```
touchstone/
├── packages/
│   ├── touchstone-core/        Python — connectors, QA, security primitives
│   ├── touchstone-mcp/         Python — MCP server, tool registration
│   ├── touchstone-cli/         Python — `touchstone` CLI (Click-based)
│   ├── touchstone-github/      TypeScript — Probot GitHub App
│   └── touchstone-ui/          TypeScript — Next.js triage UI
├── docs/                       Long-form docs (per connector, per integration)
├── examples/                   End-to-end runnable demos
├── tests/                      Unit + integration tests
├── docker/                     Dockerfiles for each surface
├── docker-compose.yml          One-command local dev stack
├── ARCHITECTURE.md             You are here
├── SECURITY.md                 Threat model, security guarantees, hardening
├── ROADMAP.md                  What's coming
├── README.md                   Elevator pitch + quickstart
└── pyproject.toml              uv workspace root for Python packages
```

We split Python and TypeScript packages because the GitHub App layer leans on Probot/Octokit, and the UI on Next.js — Python equivalents exist but the JS ecosystem is genuinely better there. Everything safety-critical is Python so the trust boundary lives in one language.

## Data flow: an MCP `query_database` call

1. **AI assistant** issues a `tools/call` for `query_database` with `{connection: "prod-ro", sql: "SELECT ..."}`.
2. **MCP server** authenticates the calling assistant (transport-level — stdio for local, OAuth2 + mTLS for remote) and routes to `touchstone.qa.query`.
3. **Policy engine** evaluates:
   - Is `prod-ro` allowed for this assistant identity?
   - Does the SQL match the allow-list grammar (default: `SELECT` + `WITH` only, no `pg_*`, no `information_schema` writes)?
   - Is the assistant under its per-minute rate budget?
   - Does the SQL touch tables tagged `sensitive`? → trigger consent gate.
4. **Consent gate** (if triggered) blocks and emits a prompt to whichever surface the operator chose: terminal, Slack, GitHub PR comment. The call sleeps until approved or times out.
5. **Connector** runs the query through a SQLAlchemy-managed read-only transaction with a hard server-side timeout and row cap. Errors are normalized into a connector-agnostic taxonomy.
6. **Result truncator** caps rows / cells / byte size before any further processing.
7. **PII detector** scans each row. Detected entities (configurable: PERSON, EMAIL, PHONE, CREDIT_CARD, SSN, IP, MEDICAL_LICENSE, custom regex/heuristics) are passed to the **masker**, which applies the per-field strategy (`redact`, `hash`, `tokenize`, `partial`).
8. **Audit logger** writes one append-only record: assistant ID, query, normalized AST, policy verdict, PII hits (counts only — never raw), row count, latency, sample of masked output. Sinks: file (default), S3, Splunk HEC, Datadog Logs, OTLP.
9. **MCP server** returns the masked result to the AI assistant.

Failure at any step short-circuits with a structured error the assistant can reason about: `policy/denied`, `consent/required`, `connector/timeout`, `pii/refused`, etc.

## The Trust Boundary, in detail

The trust boundary is one Python module — [`touchstone.security`](packages/touchstone-core/src/touchstone/security/) — and one rule: **no QA capability calls a connector except through `security.gateway.execute(...)`**. This is enforced by:

- A linter rule (Ruff custom plugin) that bans `from touchstone.connectors` imports outside `touchstone.security` and `touchstone.tests`.
- An integration test that imports every QA capability, monkeypatches `security.gateway.execute` to raise, and asserts every capability raises.

If you ever find a way around this, **that is a security bug**. File it as such.

### Policy engine

Policies are evaluated by [PyCasbin](https://github.com/casbin/pycasbin) — chosen over OPA/Rego because Casbin is embeddable (OPA needs a Go binary or REST sidecar — too heavy for a CLI), supports both RBAC and ABAC, has a mature async evaluator, and is Apache-2.0. For enterprises that already standardize on OPA, an OPA-bridge mode is available: Touchstone exports its decision context, OPA returns a verdict, Touchstone enforces.

Policies are split in two:

- **Model** (`security/policies/model.conf`) — Casbin's schema for who/what/when. Ships with sensible defaults; rarely changed.
- **Policy** (`security/policies/default.csv` and per-connection overrides) — the actual rules.

Example policy lines:

```csv
# p, subject (assistant role), object (connection or table glob), action, effect, condition
p, role:reader, conn:*-ro,    query_database, allow, ctx.sql.is_select && !ctx.sql.touches_pii
p, role:reader, conn:*,       query_database, deny,  ctx.sql.touches.contains('audit_log')
p, role:reader, conn:*,       query_database, deny,  ctx.sql.touches.contains('users.password_hash')
p, role:writer, conn:dev-*,   query_database, allow, ctx.consent_granted

# g, assistant, role
g, claude-code, role:reader
g, copilot,     role:reader
```

For operators who prefer a higher-level DSL, a YAML front-end transpiles to these CSV rules at load time:

```yaml
roles:
  reader:
    allow:
      - on: "conn:*-ro"
        when: "sql.is_select and not sql.touches_pii"
    deny:
      - on: "conn:*"
        when: "sql.touches in ('audit_log', 'users.password_hash')"
assistants:
  claude-code: [reader]
  copilot: [reader]
```

Default policy bundle ships in [`packages/touchstone-core/src/touchstone/security/policies/`](packages/touchstone-core/src/touchstone/security/policies/). Operators override or extend per-connection. Policy files are version-controlled and reviewed on change.

### PII detection

Three layers, run in order, each with a confidence score:

1. **Column-name heuristic** (free). Names matching `email`, `phone`, `ssn`, `dob`, etc. flag every value in that column. Fast, no false-negatives on well-named columns, weak on poorly-named ones.
2. **Regex bank** (~free). Stock detectors for SSN, credit card (Luhn-validated), IBAN, US phone, E.164 phone, IP, MAC, common API key formats (AWS, Stripe, GitHub PAT).
3. **Presidio NER** (~5–20 ms per row). [Microsoft Presidio](https://microsoft.github.io/presidio/) for PERSON, LOCATION, ORGANIZATION, MEDICAL_LICENSE, etc. Off by default for high-throughput queries — enabled per-connection.

Detector confidence is OR-aggregated. Anything above the configured threshold gets masked. Operators add custom detectors in 20 lines:

```python
from touchstone.security.pii import Detector, register

@register("internal_employee_id")
class EmployeeIDDetector(Detector):
    pattern = re.compile(r"\bEMP-\d{7}\b")
    def confidence(self, value: str) -> float:
        return 0.95 if self.pattern.search(value) else 0.0
```

### Masking strategies

| Strategy   | Original           | Masked                    | Use when                                  |
| ---------- | ------------------ | ------------------------- | ----------------------------------------- |
| `redact`   | `jane@acme.com`    | `[REDACTED:EMAIL]`        | LLM only needs to know the field exists.  |
| `hash`     | `jane@acme.com`    | `[H:b3f...c91]`           | LLM needs stable equality across rows.    |
| `tokenize` | `jane@acme.com`    | `EMAIL_4f2a`              | LLM needs to reason about how many distinct values, but never the values themselves. |
| `partial`  | `415-555-1212`     | `***-***-1212`            | Operator debugging convenience.           |
| `synthetic`| `Jane Doe`         | `Alex Rivers` *(stable)*  | LLM needs realistic-shaped values for test generation. Backed by [Faker]; deterministic per detector+value. |

## QA capabilities

Each capability is a single module in `touchstone.qa` and exposes both a Python API (used by CLI + GitHub App + UI) and a tool spec (consumed by the MCP server).

### `profiler` — `touchstone.qa.profiler`

Table & column profiling. For each column: type, null rate, distinct count, distinct ratio, min/max/mean/p50/p95/p99 (numeric), avg/min/max length (text), top-K values + frequencies, an inferred semantic type (id / category / freetext / timestamp / number / boolean), a few sample rows (PII-masked).

Approximate where appropriate (HyperLogLog for distinct, t-digest for percentiles) so a one-shot profile on a billion-row Snowflake table costs cents, not dollars.

### `differ` — `touchstone.qa.differ`

Compare two query results — typically dev vs prod, before-migration vs after-migration, or PR-branch vs base-branch. Three modes:

- **Schema diff** — columns added/removed/retyped.
- **Row-count diff** — fast scalar comparison with grouping support (e.g. row count *per day* over the last 30 days).
- **Value-level diff** — key-based row-by-row matching with [Datafold's primary-key data diff algorithm](https://github.com/datafold/data-diff/), reimplemented to remove the dependency and add cross-engine support.

Reports are written in three forms: structured JSON (for tooling), markdown (for PR comments), and a Rich console rendering (for CLI).

### `validator` — `touchstone.qa.validator`

A small declarative expectation language inspired by Great Expectations but cross-engine and connector-pushdown-aware. Expectations are written in TOML/YAML:

```yaml
table: orders
expectations:
  - row_count_between: {min: 1000, max: 10_000_000}
  - column_not_null: order_id
  - column_unique: order_id
  - column_values_in_set: {column: currency, set: [USD, EUR, GBP, JPY]}
  - column_freshness_seconds: {column: created_at, max: 3600}
```

Where possible, expectations are pushed down as SQL aggregates instead of pulling rows.

### `lineage` — `touchstone.qa.lineage`

Column-level lineage via [`sqlglot`](https://github.com/tobymao/sqlglot)'s lineage module. Given a target table.column, walks back through CTEs, views, and (optionally) a dbt manifest or Airflow DAG to enumerate every source column that influences it. Used by `pr.impact` to answer "if I change this column, what breaks?".

### `pr` — `touchstone.qa.pr`

Given a PR diff containing SQL changes:

1. Parse changed SQL files with `sqlglot`, normalize to a dialect-agnostic AST.
2. Cross-reference touched tables/columns against the lineage graph.
3. For each downstream consumer, run a "before vs after" `differ` on a sandbox database (if configured) or generate hypothetical impact (if not).
4. Emit a structured report: tables created, dropped, mutated; columns retyped; downstream queries that will break; tests that should be added.

The report is what gets posted as the GitHub PR comment and rendered in the Triage UI.

### `test_gen` — `touchstone.qa.test_gen`

LLM-assisted test case synthesis. Profiles a table, identifies invariants (uniqueness, foreign-key integrity, value ranges, freshness), then proposes expectations in the `validator` language. The proposals are *not* auto-applied — they land as a PR or CLI diff for human approval.

The LLM call goes through a small adapter so operators can point it at the same provider their AI assistant uses (Claude, OpenAI, Azure OpenAI, Bedrock, self-hosted) or disable it entirely and use only profile-derived heuristics.

## Surfaces

### MCP server

Built on the [official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk). Tools are registered declaratively from the QA capabilities — adding a new tool is a decorator. Supports stdio transport (the local default) and Streamable HTTP for remote deployment behind an OAuth2 proxy.

### CLI

[Click](https://click.palletsprojects.com/) + [Rich](https://rich.readthedocs.io/). Commands mirror the QA capabilities. Designed to be useful standalone — you don't have to run the MCP server to use Touchstone.

### GitHub App

[Probot](https://probot.github.io/) (TypeScript). Listens for `pull_request.opened/synchronize`, runs `pr.impact` against the diff, posts a structured comment, and updates a check run. App-installation tokens are scoped per repo; the app never sees data directly — it shells out to a `touchstone-core` worker running in the customer's environment.

### Triage UI

Next.js 15 + Tailwind + shadcn/ui. Renders findings, lets a human approve/reject, and writes the decision into the feedback log so the policy engine learns. Read-only by default — write actions require step-up auth.

## Deployment topologies

### 1. Pure local (developer laptop)

```
[Claude Code] ──stdio──► [touchstone-mcp]
                              │
                              ▼
                  [DuckDB / local Postgres / read-only SSH tunnel to staging]
```

This is the recommended starting point. Zero external dependencies, instant feedback, sample data ships in the box.

### 2. Self-hosted in customer VPC

```
[Developers' assistants] ──Streamable HTTP + OAuth2──► [Touchstone Gateway]
                                                              │
                                                  ┌───────────┼───────────┐
                                                  ▼           ▼           ▼
                                            [Snowflake] [Postgres] [Audit Sink]
```

Touchstone runs as a small fleet behind your existing identity proxy (Pomerium, Cloudflare Zero Trust, Okta, etc.). Audit logs ship to the customer's SIEM. No data crosses the VPC boundary.

### 3. CI / GitHub Actions

```
[GitHub PR] ──webhook──► [touchstone-github] ──spawns──► [touchstone-core worker in CI runner]
                                                                       │
                                                                       ▼
                                                                  [Sandbox DB]
```

The PR report is generated entirely inside the customer's CI runner — the GitHub App is just the orchestrator.

## Non-goals

- **We are not a data observability platform.** Monte Carlo, Acceldata, Bigeye do that better. Touchstone is for *PR-time* and *ad-hoc* QA, not 24/7 monitoring.
- **We are not a transformation framework.** dbt / SQLMesh / Dataform are how you build pipelines. Touchstone is how you QA the PRs that change those pipelines.
- **We are not a vendor-hosted SaaS.** Optional managed offerings may exist later, but the OSS is the product.

## Open architectural questions

- **Streaming results.** Today, results materialize before masking. Some Snowflake queries return GB. Plan: row-streaming masker with a backpressure-aware MCP transport. Tracked in [ROADMAP.md](ROADMAP.md).
- **Dialect-agnostic lineage at scale.** `sqlglot` handles 90% of cases; the long tail of vendor extensions (Snowflake `MATCH_RECOGNIZE`, BigQuery `ML.PREDICT`) needs per-dialect work.
- **Cross-DB diff.** Postgres ↔ Snowflake diffs are common during migrations. Hash-partition algorithm works, but we need a shared canonical type model. WIP.

See [`ROADMAP.md`](ROADMAP.md) for the prioritized list.
