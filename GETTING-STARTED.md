# Getting Started

A real 5-minute path from "I just cloned this" to "my AI assistant is
running QA against my database."

Three audiences, three paths:

1. [I'm a solo developer trying it on my laptop](#1-solo-developer-on-a-laptop) — DuckDB + a CSV.
2. [I'm wiring it up at my company against a real DB](#2-team-with-a-real-database) — Postgres / Snowflake.
3. [I'm an enterprise platform team deploying it for everyone](#3-enterprise-multi-tenant-deployment) — multi-tenant, OAuth, audit forwarding.

## Prerequisites

- **Python 3.11 or newer**.
- **An AI assistant that speaks MCP**: Claude Code, Cursor, Continue, GitHub
  Copilot (with the MCP preview), or any [MCP-compliant client](https://modelcontextprotocol.io/clients).
- **Optional**: Docker (only for the local dev DB stack), Playwright (only
  for web automation).

> Touchstone is **not yet on PyPI** — install from GitHub. See [#27](https://github.com/VibhavSetlur/Touchstone/issues/27) for tracking.

---

## 1. Solo developer on a laptop

The fastest possible path. Zero external dependencies.

```bash
# 1. Get the source.
git clone https://github.com/VibhavSetlur/Touchstone.git
cd Touchstone

# 2. Install. Auto-picks uv / pipx / pip in that order.
./install.sh

# 3. Make a config + a sample DuckDB file.
touchstone init --non-interactive
python3 - <<'EOF'
import duckdb
c = duckdb.connect("/tmp/shop.duckdb")
c.execute("CREATE TABLE customers (id BIGINT, email VARCHAR, name VARCHAR)")
c.execute("INSERT INTO customers VALUES (1, 'jane@example.com', 'Jane'), (2, 'john@example.com', 'John')")
c.close()
EOF
cat > ~/.touchstone/config.toml <<'EOF'
[connections.local-duck]
engine = "duckdb"
database = "/tmp/shop.duckdb"
read_only = true
tags = ["dev"]

[security]
pii_threshold = 0.4
pii_default_strategy = "redact"
pii_detectors_enabled = ["column_name", "regex"]

[[security.audit_sinks]]
kind = "file"
path = "~/.touchstone/audit.jsonl"

[knowledge]
path = "~/.touchstone/knowledge.db"

[llm]
provider = "none"
model = ""
EOF

# 4. Verify everything is working.
touchstone --version
touchstone doctor
touchstone profile local-duck customers
# → you should see [REDACTED:EMAIL] in place of jane@example.com
```

**Now wire it up to Claude Code** (or your assistant of choice — see
[`docs/integrations/`](docs/integrations/) for Cursor, Continue, Copilot):

Add to `~/.claude/mcp_servers.json` (create if missing):

```json
{
  "mcpServers": {
    "touchstone": {
      "command": "touchstone-mcp",
      "args": ["--config", "/home/USER/.touchstone/config.toml"]
    }
  }
}
```

Restart Claude Code and ask: *"What tables are in `local-duck`? Profile
the customers table and tell me what PII you see."*

You should see Claude call `list_tables` then `profile_table_tool`, and
the result will have `pii_findings_summary: {EMAIL: 2}` — Claude sees the
*type* of PII but never the values.

---

## 2. Team with a real database

For Postgres, MySQL, Snowflake, BigQuery, Mongo, Databricks, etc.

```bash
git clone https://github.com/VibhavSetlur/Touchstone.git
cd Touchstone

# Install with the connector you need:
./install.sh
# Or: TOUCHSTONE_EXTRAS=snowflake,postgres,bigquery ./install.sh
```

Then a `~/.touchstone/config.toml` like:

```toml
[connections.warehouse-ro]
engine = "snowflake"
user = "touchstone_svc"
database = "PROD"
schema = "PUBLIC"
password_ref = "env://SNOWFLAKE_PASSWORD"   # NEVER plaintext
read_only = true
tags = ["prod"]
row_cap = 5000
timeout_seconds = 60

[connections.warehouse-ro.extra]
account = "acme"
warehouse = "TOUCHSTONE_XS"
role = "TOUCHSTONE_RO"
auth = "key_pair"
private_key_file = "/etc/touchstone/snowflake_rsa.p8"

[connections.dev-pg]
engine = "postgres"
host = "dev-db.acme.internal"
port = 5432
database = "shop"
user = "touchstone_ro"
password_ref = "vault://shop/touchstone/password"
read_only = true
tags = ["dev"]

[security]
pii_default_strategy = "redact"
pii_detectors_enabled = ["column_name", "regex", "presidio"]
consent_required_on = ["non_select", "tagged:prod", "large_result"]

[[security.audit_sinks]]
kind = "file"
path = "/var/log/touchstone/audit.jsonl"

[[security.audit_sinks]]
kind = "s3"
bucket = "acme-touchstone-audit"
prefix = "audit/"

[cost]
max_estimated_rows = 50_000_000
max_estimated_bytes = 5_368_709_120   # 5 GB
large_tables = ["events", "raw_logs"]
concurrent_cap_per_assistant = 4

[sensitivity.warehouse-ro]
redact = ["users.notes", "support.tickets.body"]
never_leaves = ["medical.records.*"]
hash = ["users.email"]
```

Then:

```bash
# Verify everything resolves (secrets, connections, audit).
touchstone doctor

# Try a real query (PII auto-masked, cost guard active).
touchstone profile warehouse-ro orders

# Wire into your team's AI assistant — same MCP config as above.
```

For **MFA-protected dashboards** (Looker, Tableau, Grafana):

```bash
# One-time per credential, opens a headed browser:
export TOUCHSTONE_SESSION_KEY="$(openssl rand -hex 24)"
touchstone session bootstrap looker_admin
# (complete MFA in the browser window, press ENTER)

# Now headless calls reuse the encrypted session:
touchstone playbook run dashboard_verify --params '{...}'
```

Full walkthrough: [`docs/integrations/mfa-bootstrap.md`](docs/integrations/mfa-bootstrap.md).

---

## 3. Enterprise multi-tenant deployment

Run one Touchstone server per VPC behind your OAuth/SAML proxy. Each
team's AI assistants connect over Streamable HTTP; their identity drops
out of the proxy as a contextvar; tenant isolation kicks in.

```bash
# Production image:
docker build -t touchstone-mcp:0.1 -f docker/touchstone-mcp.Dockerfile .

# Or Helm (planned for v0.2):
# helm install touchstone deploy/helm \
#     --set image.repository=touchstone-mcp \
#     --set audit.s3.bucket=acme-touchstone-audit
```

Minimum config for multi-tenant:

```toml
[connections.finance-ro]
engine = "snowflake"; ...

[connections.growth-ro]
engine = "snowflake"; ...

[tenants.team-finance]
connections = ["finance-ro"]
policy_files = ["/etc/touchstone/policies/finance.yaml"]
consent_channel = "slack:finance-alerts"

[tenants.team-growth]
connections = ["growth-ro"]
policy_files = ["/etc/touchstone/policies/growth.yaml"]

[security]
audit_sinks = [
    {kind = "file", path = "/var/log/touchstone/audit.jsonl"},
    {kind = "s3", bucket = "acme-touchstone-audit", prefix = "audit/"},
]

[web]
allowed_origins = [
    "https://looker.acme.com",
    "https://tableau.acme.com",
]
```

Identity flows in via your existing auth proxy (Pomerium, Cloudflare Zero
Trust, Authelia, Authentik, Okta) which sets headers like `X-Auth-Subject`
and `X-Auth-Group`. Touchstone reads these via a contextvar middleware
([`packages/touchstone-mcp/src/touchstone_mcp/identity.py`](packages/touchstone-mcp/src/touchstone_mcp/identity.py)).

Full enterprise walkthrough:

- [`docs/deployment/README.md`](docs/deployment/README.md)
- [`docs/security/multi-tenant.md`](docs/security/multi-tenant.md)
- [`docs/security/compliance-mapping.md`](docs/security/compliance-mapping.md) (SOC 2 / HIPAA / GDPR / PCI control mapping)

---

## What to do after the install works

1. **Run [`tests/`](tests/)** — `make test` — proves the safety properties
   on your install. 69 unit + security tests should pass.
2. **Read [`SECURITY.md`](SECURITY.md)** and the credential-blindness
   contract: [`docs/security/ai-credential-blindness.md`](docs/security/ai-credential-blindness.md).
3. **Read the [architectural vulnerabilities review](docs/security/architectural-vulnerabilities.md)**
   if you're evaluating Touchstone against alternatives — it's an honest
   audit of what could go wrong and how each is mitigated.
4. **Try a playbook**: `touchstone playbook list` — pick `migration_safety`
   or `data_handoff` and run against a real table you know.
5. **Open an issue** on https://github.com/VibhavSetlur/Touchstone for
   anything that doesn't work as advertised.

---

## Honest status

- **Beta**: `touchstone-core`, `touchstone-mcp`, `touchstone-cli`.
  Tested against Postgres / DuckDB / SQLite end-to-end. Snowflake /
  BigQuery / Databricks have working connector code but limited
  field testing.
- **Alpha**: `touchstone-github` (Probot app), `touchstone-ui` (Next.js
  triage UI). Scaffolds that work, but minimal.
- **Roadmap**: PyPI release, Helm chart, vector-search over knowledge,
  visual diff for dashboards. See [`ROADMAP.md`](ROADMAP.md).

If you hit anything that doesn't work — even a typo in a doc — file an
issue. We respond.
