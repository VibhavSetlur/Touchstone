# touchstone-mcp

Touchstone MCP server — exposes Touchstone's QA capabilities as MCP tools
so any AI assistant (Claude Code, Cursor, Continue, Copilot, etc.) can
call them.

## Install

```bash
pip install -e packages/touchstone-core[quickstart] -e packages/touchstone-mcp
```

## Run

```bash
touchstone-mcp                                # stdio (local)
touchstone-mcp --transport streamable-http    # remote, behind an OAuth proxy
```

## Wire into Claude Code

Add to `~/.claude/mcp_servers.json`:

```json
{
  "mcpServers": {
    "touchstone": {
      "command": "touchstone-mcp",
      "args": ["--config", "~/.touchstone/config.toml"]
    }
  }
}
```

See [docs/integrations/](https://github.com/VibhavSetlur/Touchstone/tree/main/docs/integrations) for Cursor, Continue, Copilot.

## License

Apache-2.0.
