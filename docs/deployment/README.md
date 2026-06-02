# Deployment guide

Touchstone supports three topologies. Pick by team size and risk profile.

## 1. Pure local (developer laptop)

Each developer runs `touchstone-mcp` over stdio under their AI assistant.
No external service, no shared infra.

**Good for:** trying it out, single-developer projects, local-only data
(DuckDB / SQLite / a personal Postgres sandbox).

**Setup:** [`examples/postgres-quickstart`](../../examples/postgres-quickstart/README.md).

**Tradeoff:** every dev maintains their own config. Audit logs are local.
Hard to standardize policy across a team.

## 2. Team / enterprise (one server per environment)

Touchstone runs as a service (per VPC, per env) behind an OAuth2 proxy.
Developers' AI assistants connect over Streamable HTTP.

```
[Developers' assistants] ──HTTPS + OAuth2──► [Auth proxy] ──► [Touchstone MCP]
                                                                     │
                                                            ┌────────┴────────┐
                                                            ▼                 ▼
                                                       [Snowflake]       [S3 audit]
```

**Good for:** any team where you want one policy bundle, one audit sink,
one consent queue.

**Setup:** [`examples/snowflake-enterprise`](../../examples/snowflake-enterprise/README.md).

**Auth proxies known to work:** Authelia, Authentik, Pomerium, Cloudflare
Zero Trust, Okta IAP, Azure AD App Proxy.

**Tradeoff:** one more service to run. Slightly higher latency than local.
Get audit centralization, policy standardization, and per-team identity
in return.

## 3. CI / GitHub Actions

The GitHub App spawns a `touchstone-cli` worker in the runner. No
persistent service.

```
[GitHub PR] ──webhook──► [touchstone-github] ──spawn──► [touchstone-cli in CI runner]
                                                              │
                                                              ▼
                                                       [Sandbox DB]
```

**Good for:** PR comments, sandbox testing as part of CI.

**Setup:** [`docs/integrations/github.md`](../integrations/github.md).

**Tradeoff:** CI-only, no interactive sessions. Pair with topology 1 or 2
for the interactive case.

## Sizing & resources

| Workload                                | CPU | Mem  | Notes                              |
| --------------------------------------- | --- | ---- | ---------------------------------- |
| MCP server, ≤5 devs                     | 1   | 512M | Run on a t3.small.                 |
| MCP server, 50 devs, prod Snowflake     | 2   | 2G   | Network is the bottleneck.         |
| GitHub App, 100 repos                   | 1   | 1G   | CPU spikes during PR-impact runs.  |
| Triage UI                               | 0.5 | 256M | Static-ish; Next.js streaming.     |

## Hardening checklist

- [ ] Touchstone runs as a non-root user (Dockerfiles already do this).
- [ ] DB credentials come from a secret manager — never inline in config.
- [ ] Audit logs ship to an off-host sink (S3 with object-lock, Splunk HEC, etc.).
- [ ] Consent prompts route somewhere a human will see them (Slack DM, not
      a stale terminal session).
- [ ] Policy files are checked into version control and reviewed on change.
- [ ] DB role for Touchstone is read-only at the DB level (not just trust
      the SQL allow-list).
- [ ] PII detection includes Presidio in addition to regex (slower but
      catches NER cases).
- [ ] Streamable HTTP endpoint is behind an OAuth2 proxy — never on the
      open internet.
- [ ] Renovate / Dependabot is enabled on the deployment repo so security
      updates land fast.

## Backup / DR

The only state Touchstone holds is the audit log. If you've configured an
off-host sink (recommended), local audit files are safe to lose. Restoring
to a new host: copy `~/.touchstone/config.toml`, ensure secret references
resolve, restart. There is no DB to back up.
