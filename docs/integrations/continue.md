# Integrating with Continue

[Continue](https://continue.dev/) supports MCP servers. Same Touchstone
server, different config syntax.

## Setup

1. Install Touchstone:

   ```bash
   git clone https://github.com/VibhavSetlur/Touchstone.git
   cd Touchstone && ./install.sh
   touchstone init
   touchstone doctor
   ```

2. Edit `~/.continue/config.yaml`:

   ```yaml
   mcpServers:
     - name: touchstone
       command: touchstone-mcp
       args:
         - --config
         - /Users/YOU/.touchstone/config.toml
       env:
         TOUCHSTONE_ASSISTANT_ID: continue@you@acme
   ```

3. Reload Continue (Cmd+Shift+P → "Continue: Reload Configuration").

## Verify

Open the Continue chat and prompt:

```
List the connections from the touchstone MCP server, then profile any
one table you find.
```

Continue should call `list_connections` then `profile_table_tool`.

## Tips specific to Continue

- Continue exposes MCP tools by namespace; ours appear as `touchstone.*`.
- Continue auto-restarts MCP servers when their config changes — handy
  for iterating on `touchstone.toml`.
- If you use Continue's slash commands, you can wire `/qa` to a Touchstone
  playbook via Continue's custom slash command syntax.
