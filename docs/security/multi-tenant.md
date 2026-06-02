# Multi-tenant isolation

Touchstone is designed to run as a single instance serving multiple
teams or clients. This document explains how isolation is enforced and
what operators must do to keep it working.

## The guarantees

- Tenant B's queries never run on a connector instance that Tenant A
  has used.
- Tenant B cannot address a connection that's not in Tenant B's
  manifest, even if the AI tries to name one.
- Tenant B's audit records carry `tenant_id=B`; tampering with the chain
  to insert wrong-tenant records breaks `verify-rotated`.
- Tenant-scoped policy overrides apply per-call.

## How it's enforced

### 1. `tenant_id` arrives via authenticated transport

For **stdio** (local single-user MCP): the env var
`TOUCHSTONE_TENANT_ID` is read at process start. Single tenant.

For **Streamable HTTP** behind an OAuth/OIDC proxy: each request carries
the subject claim in a header (e.g. `X-Auth-Subject` set by your
Pomerium / Cloudflare Zero Trust / Auth proxy). A small middleware in
`touchstone_mcp.identity` puts it in a `contextvar`. Every tool call
reads from the contextvar, so two concurrent requests under different
auth get different tenants.

**The AI never sets the tenant.** There is no MCP tool that accepts a
`tenant_id` parameter. If a misconfigured proxy fails to set the
identity header, the call lands as `tenant_id="default"` — which (in a
multi-tenant deployment) is configured to be denied for any real work.

### 2. `ConnectorPool` keyed by `(tenant_id, connection_name)`

```python
pool.get("tenant_a", prod_ro_cfg)  # → connector instance A
pool.get("tenant_b", prod_ro_cfg)  # → DIFFERENT connector instance B
```

A and B are distinct Python objects, distinct DB sessions, distinct
in-memory caches. There is no code path that hands a tenant-A connector
to a tenant-B call.

Per-tenant LRU eviction prevents one tenant from exhausting the pool.

### 3. Manifest filters every reference

```toml
[tenants.team-finance]
connections = ["finance-ro", "warehouse-ro"]
policy_files = ["/etc/touchstone/policies/finance.yaml"]
consent_channel = "slack:finance-alerts"

[tenants.team-growth]
connections = ["growth-ro"]
policy_files = ["/etc/touchstone/policies/growth.yaml"]
```

When `team-finance` calls `query_database(connection="growth-ro", ...)`:

```
PolicyDeniedError: connection 'growth-ro' is not in tenant 'team-finance''s manifest.
```

The check happens before any connector is touched and before any policy
rule fires. It's a structural deny.

### 4. Audit records are tenant-tagged

Every record's `sql_ast_summary` includes `tenant_id`. The hash chain
binds `tenant_id` into each record's hash. An attacker who tries to
forge a record claiming a different tenant will break the chain at the
next legitimate write.

## What operators must do

1. **Run one MCP server per tenant, OR run a single server behind a
   proper identity-aware proxy.** Don't run a single server with
   `TOUCHSTONE_TENANT_ID=default` and expect it to be multi-tenant.
2. **Use a dedicated DB role per tenant.** Touchstone's tenant
   isolation is at the application layer; pair it with row-level
   security / role isolation at the DB layer for defense in depth.
3. **Keep tenant manifests in version control.** When you onboard a
   new team, the manifest change is a PR.
4. **Ship per-tenant audit slices to per-tenant SIEMs.** The audit log
   has `tenant_id`; filter on it when forwarding.

## A worked example

Single Touchstone server, three tenants.

```toml
# /etc/touchstone/config.toml

[connections.finance-ro]
engine = "snowflake"; user = "touchstone_finance"; ...

[connections.growth-ro]
engine = "snowflake"; user = "touchstone_growth"; ...

[connections.platform-ro]
engine = "postgres"; user = "touchstone_platform"; ...

[tenants.team-finance]
connections = ["finance-ro"]

[tenants.team-growth]
connections = ["growth-ro"]

[tenants.team-platform]
connections = ["platform-ro"]
```

Behind Pomerium, every request includes the user's Google Workspace
group. A small middleware maps:

| Group                  | Tenant            |
|------------------------|-------------------|
| `team-finance@acme.com`| `team-finance`    |
| `team-growth@acme.com` | `team-growth`     |
| `team-platform@acme.com`| `team-platform`  |

Three Claude Code instances on three laptops, all connecting to the
same Touchstone endpoint. Each gets the connections their team can
see, audit records segregate by tenant, and a panicked AI on the growth
team can't touch finance data — because the connection name isn't even
in its manifest.

## What this does NOT do

- Magic. Operators still need to set up DB roles per team, audit
  forwarding per team, policy bundles per team. Touchstone enforces the
  isolation; it doesn't configure it for you.
- Cross-tenant joins. If a workflow genuinely needs to join across
  tenants, the operator builds a dedicated cross-tenant tenant with an
  appropriately permissioned DB role. There's no implicit cross-tenant
  access.
