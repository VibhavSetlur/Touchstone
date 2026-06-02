# Integrating with GitHub Copilot

GitHub Copilot's MCP support is currently in preview (as of 2026). The
config lives in your VS Code `settings.json` under `github.copilot.mcp`.

## Setup

1. Install Touchstone:

   ```bash
   git clone https://github.com/VibhavSetlur/Touchstone.git
   cd Touchstone && ./install.sh
   touchstone init
   touchstone doctor
   ```

2. In VS Code, open `Settings` → `Open settings (JSON)` and add:

   ```json
   {
     "github.copilot.mcp.servers": {
       "touchstone": {
         "command": "touchstone-mcp",
         "args": ["--config", "/Users/YOU/.touchstone/config.toml"],
         "env": {
           "TOUCHSTONE_ASSISTANT_ID": "copilot@you@acme"
         }
       }
     }
   }
   ```

3. Reload VS Code (Cmd+Shift+P → "Developer: Reload Window").

4. In a Copilot Chat session, type `@` — Touchstone tools should appear
   in the participant list.

## Verify

Ask Copilot in chat:

```
@touchstone list connections, then describe the first table you find.
```

Copilot will call `list_connections` followed by `describe_table` and
return the results to you.

## Tips specific to Copilot

- Copilot's MCP preview is gated — check
  [github/feedback](https://github.com/orgs/community/discussions/categories/copilot-feedback)
  for current availability in your tier (Business / Enterprise).
- Tool call display in Copilot is more conservative than Claude Code — if
  you don't see what tools were called, check the Output panel under
  "GitHub Copilot Chat".
- For team-wide rollout, ship the settings.json fragment via VS Code
  workspace settings or a Settings Sync profile.
