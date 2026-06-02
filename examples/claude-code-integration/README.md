# Claude Code integration

Connect Touchstone to Claude Code so the assistant can run audited queries
during a conversation.

## 1. Install

```bash
pipx install touchstone-mcp     # or: uv tool install touchstone-mcp
touchstone init
```

## 2. Register the MCP server

Edit `~/.claude/mcp_servers.json` (create if missing):

```json
{
  "mcpServers": {
    "touchstone": {
      "command": "touchstone-mcp",
      "args": ["--config", "/Users/you/.touchstone/config.toml"],
      "env": {
        "TOUCHSTONE_ASSISTANT_ID": "claude-code"
      }
    }
  }
}
```

## 3. Verify

Start Claude Code and check that the tools appear:

```
> /mcp
touchstone:
  - list_connections
  - list_tables
  - describe_table
  - query_database
  - profile_table_tool
  - diff_environments_tool
  - check_data_quality_tool
  - explain_lineage_tool
  - analyze_pr_data_impact_tool
  - generate_test_cases_tool
```

## 4. Try it

```
> Profile the `customers` table on local-pg and summarize the PII you find.
```

Claude will call `profile_table_tool`. PII will be masked before it gets to
Claude — you'll see categories ("EMAIL: 4", "PHONE: 3") in the output, but
not raw values.

## 5. Audit

```bash
touchstone audit tail
touchstone audit verify
```

Every tool call Claude made is recorded, hash-chained.

## Tips

- For team installs, distribute the `touchstone.toml` via your config-management
  tooling. Each developer's MCP entry should point at it.
- To restrict what Claude can do, edit `security.policy_files` in the config.
- To approve a sensitive op without a Slack/webhook channel set up, run
  Touchstone in the foreground — terminal consent prompts will appear in
  stderr.
