# MFA / SSO session bootstrap

Enterprise BI tools live behind Okta, Duo, Ping, Microsoft, etc. None of
these will let a headless browser through. The `touchstone session bootstrap`
flow solves it once per credential, then headless automation reuses the
captured session forever (or until the SSO session expires).

## The flow

1. **One-time setup.** Operator sets `TOUCHSTONE_SESSION_KEY` — a strong
   passphrase used to encrypt every stored session. We refuse to store
   plaintext.

   ```bash
   # Local dev (operator machine):
   export TOUCHSTONE_SESSION_KEY="$(openssl rand -hex 24)"
   echo "$TOUCHSTONE_SESSION_KEY" >> ~/.zshrc

   # Server deployment: pull from vault.
   export TOUCHSTONE_SESSION_KEY="$(vault kv get -field=key touchstone/session)"
   ```

2. **Configure the credential** in `touchstone.toml`:

   ```toml
   [web.credentials.looker_admin]
   login_url = "https://looker.acme.com/login"
   username  = "env://LOOKER_USER"
   password  = "env://LOOKER_PASSWORD"
   # Optional: override login form selectors.
   username_selector = 'input[name="email"]'
   password_selector = 'input[type="password"]'
   submit_selector   = 'button[type="submit"]'
   ```

   Note: `username` / `password` are still useful — the bootstrap flow
   prefills them so the human just clicks "approve" on the MFA push.

3. **Run bootstrap.** Opens a *headed* browser window.

   ```bash
   $ touchstone session bootstrap looker_admin

   Opening browser for credential 'looker_admin'...
     Login URL: https://looker.acme.com/login
     Complete login (including MFA push, TOTP, SSO redirects).

   Press ENTER once you've completed login (including MFA): _
   ```

   The operator logs in interactively — completes the Duo push, types
   the TOTP, whatever's needed. When they're on the post-login page,
   they press ENTER.

4. **Touchstone captures the session.** Playwright's `storage_state`
   (cookies + localStorage) is encrypted with AES-GCM-256 (key derived
   from `TOUCHSTONE_SESSION_KEY` via PBKDF2-HMAC-SHA256, 200k
   iterations, per-credential salt) and stored at
   `~/.touchstone/web-sessions/looker_admin.aesgcm`.

5. **Subsequent headless calls reuse it.** Any `browse` / `verify_dashboard`
   call with `use_stored_session_for: "looker_admin"` loads the
   encrypted state and lands on the post-login page directly. No MFA
   prompt. The AI never touches the encrypted file, the key, or the
   cookies — those only exist inside the gateway/browser process.

## When the session expires

SSO sessions expire (typically 8-24 hours; sometimes weeks with refresh
tokens). When that happens, the next headless call lands on a login page.
Touchstone detects this via `looks_like_login_page` / `looks_like_mfa_challenge`
heuristics, marks the session **expired**, and returns:

```json
{
  "ok": false,
  "session_state": "expired",
  "error": "session expired: landed on 'https://acme.okta.com/login'; ..."
}
```

The AI sees this and asks the operator to re-bootstrap. The MCP server
exposes `session_is_valid` and `list_stored_sessions` tools so the AI can
check proactively.

## How the AI cannot abuse this

- `bootstrap` is a CLI-only command. There is no MCP tool that triggers
  it.
- `TOUCHSTONE_SESSION_KEY` is in the gateway process's env, not passed
  through MCP.
- The encrypted file is only readable by the operating user (0600).
- The decrypted `storage_state` lives in the Playwright context for one
  tool call, then is dropped when the session closes.

## Re-bootstrap workflow

```bash
# When the AI reports `session_state=expired`:
$ touchstone session list
  looker_admin
  tableau_finance

$ touchstone session delete looker_admin     # optional, cleans up the old file
$ touchstone session bootstrap looker_admin  # operator does the dance
```

Or run from the Triage UI (planned alpha v0.2): a button that opens the
bootstrap flow under the operator's account, no terminal needed.

## What if I can't do interactive bootstrap

Some servers genuinely can't run headed Chromium (CI agents, container
fleets). Two paths:

1. **One operator bootstraps from a laptop**, then commits the encrypted
   session blob to a private vault keyed under `TOUCHSTONE_SESSION_KEY`.
   Other deployment nodes pull the blob at startup. The blob is useless
   without the key.
2. **Service-account API keys** where the BI tool supports them (Looker
   API key, Tableau personal-access-token). Configure as
   `credential.token = "vault://..."` and the browser does a single-step
   token-header auth instead of a UI login. No MFA needed at all.
   Coverage is BI-vendor-dependent.
