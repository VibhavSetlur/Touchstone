# Integrating with Cursor

Cursor supports MCP servers as of v0.40. Same Touchstone server, different
config path.

## Setup

1. Install Touchstone:

   ```bash
   git clone https://github.com/VibhavSetlur/Touchstone.git
   cd Touchstone && ./install.sh
   touchstone init                # one-time
   touchstone doctor              # verify
   ```

2. Open Cursor → `Settings` → `MCP` → `Add new MCP server`. Or edit
   `~/.cursor/mcp.json` directly:

   ```json
   {
     "mcpServers": {
       "touchstone": {
         "command": "touchstone-mcp",
         "args": ["--config", "/Users/YOU/.touchstone/config.toml"],
         "env": {
           "TOUCHSTONE_ASSISTANT_ID": "cursor@you@acme"
         }
       }
     }
   }
   ```

3. Restart Cursor. Open the MCP panel — Touchstone should appear with
   ~30 tools listed.

## Verify

Open Cursor's chat and prompt:

```
What MCP tools do you have from touchstone? Use list_connections to show
the configured connections.
```

You should see Cursor call `list_connections` and report what's in your
config — no credentials returned, just names + engines.

## Tips specific to Cursor

- Cursor's chat sometimes batches many tool calls; Touchstone's
  concurrency cap (default 4) keeps this from spawning runaway parallel
  scans.
- For per-project Touchstone configs, set `TOUCHSTONE_CONFIG` in your
  project's `.cursor/env` or set the `args` per-workspace.
- If Cursor doesn't see the tools, run `touchstone serve-mcp` manually
  to confirm the server actually starts.
