# Integrating with Claude Code

See [`examples/claude-code-integration/README.md`](../../examples/claude-code-integration/README.md)
for the quickstart. This page covers deeper integration patterns.

## Per-project assistant identity

By default, Touchstone uses `TOUCHSTONE_ASSISTANT_ID=anonymous`. Set it
per-project so audit logs and policies can distinguish callers:

```json
{
  "mcpServers": {
    "touchstone": {
      "command": "touchstone-mcp",
      "args": ["--config", "/abs/path/to/.touchstone/config.toml"],
      "env": {
        "TOUCHSTONE_ASSISTANT_ID": "claude-code@alice@acme",
        "TOUCHSTONE_SESSION_ID": "${env:GITHUB_RUN_ID:-local}"
      }
    }
  }
}
```

Policy rules can then target this identity:

```yaml
  - name: alice-can-touch-finance
    effect: allow
    assistants: ["claude-code@alice@*"]
    connections: ["finance-ro"]
    priority: 50
```

## Multiple connections, one MCP server

A single Touchstone MCP server can serve many connections — there's no need
to register one MCP server per database. The assistant picks the connection
in each call:

```
> Query the orders table on `prod-ro`. Then diff its schema against `dev-ro`.
```

Claude will issue:

- `query_database({connection: "prod-ro", sql: "SELECT * FROM orders LIMIT 5"})`
- `diff_environments_tool({left_connection: "dev-ro", right_connection: "prod-ro", ...})`

## Pinning context for the assistant

The MCP server can be configured to attach a system-prompt-style hint via
`Resources` (not enabled by default; opt in with `--expose-resources`):

```toml
[[mcp.resources]]
uri = "touchstone://policy/summary"
name = "Touchstone policy summary"
description = "Tells the assistant what it can and cannot do."
```

This way the assistant doesn't waste calls trying things that will be denied.

## Local development with Claude Code

For local dev (no live DB connection wanted): the `touchstone init` wizard
configures a DuckDB connection by default. Even with zero external DBs,
Claude Code can profile / diff / validate / test-gen against `.parquet` /
`.csv` / `.sqlite` files.
