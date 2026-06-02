# Web automation

Touchstone can drive a headless browser to do work that used to be manual
clicking — log into a dashboard, click a filter, screenshot a chart,
extract a table, verify it matches the DB.

## Setup

```bash
pip install 'touchstone-core[web]'
playwright install chromium
```

Then in `touchstone.toml`:

```toml
[web]
allowed_origins = [
    "https://looker.acme.com",
    "https://app.acme-internal.com",
]
headless = true
# Optional persistent context (cookies + local storage).
# Useful when SSO would otherwise require a fresh login every session.
context_dir = "~/.touchstone/web-context"

[web.credentials.looker_admin]
username = "env://LOOKER_USER"
password = "env://LOOKER_PASSWORD"
# Optional override of the auto-detected login form selectors:
username_selector = 'input[name="email"]'
password_selector = 'input[name="password"]'
submit_selector = 'button[type="submit"]'
```

## What the AI sees

When the AI lists what's available it sees only **names** — never URLs and
never credentials:

```json
{
  "allowed_origins": ["https://looker.acme.com", "https://app.acme-internal.com"],
  "credentials": ["looker_admin"]
}
```

To log in, it issues a step like:

```json
{"op": "login", "url": "https://looker.acme.com", "credential": "looker_admin"}
```

Touchstone resolves `env://LOOKER_USER` and `env://LOOKER_PASSWORD` at
fill-time, types them, scrubs them from any echoed page text, and returns
`"[logged in]"` as the step result. The AI never sees the raw values.

## Step reference

| Op                 | Required          | Optional                          | Effect                                       |
| ------------------ | ----------------- | --------------------------------- | -------------------------------------------- |
| `navigate`         | `url`             | `timeout_ms`                      | Go to a URL (must match `allowed_origins`).  |
| `click`            | `selector`        | `timeout_ms`                      | Click the matched element.                   |
| `fill`             | `selector` + (`value` or `credential`) | `timeout_ms`, `field_role` | Type a value. Refused if literal + password field. |
| `select`           | `selector`, `value` | `timeout_ms`                    | Select an `<option>` by value.               |
| `press`            | `value` (key)     | `selector`, `timeout_ms`          | Press a key.                                 |
| `wait_for_selector`| `selector`        | `timeout_ms`                      | Block until the selector appears.            |
| `wait_for_url`     | `url`             | `timeout_ms`                      | Block until URL matches.                     |
| `screenshot`       | —                 | `metadata.path`, `metadata.full_page` | Save a screenshot.                       |
| `extract_text`     | —                 | `selector`                        | Inner text of selector (PII-masked).         |
| `extract_table`    | —                 | `selector` (default `table`)      | Parse an HTML table to rows.                 |
| `list_buttons`     | —                 | —                                 | Enumerate clickable elements.                |
| `list_inputs`      | —                 | —                                 | Enumerate form fields.                       |
| `login`            | `credential`      | `url`, `timeout_ms`               | High-level login flow.                       |

## A typical session

```json
[
  {"op": "navigate", "url": "https://looker.acme.com/dashboards/42"},
  {"op": "login", "credential": "looker_admin"},
  {"op": "wait_for_selector", "selector": ".dashboard-grid"},
  {"op": "list_buttons"},
  {"op": "click", "selector": "button[data-testid='filter-time-range']"},
  {"op": "click", "selector": "li[data-value='last_7_days']"},
  {"op": "extract_table", "selector": "table.results-table"}
]
```

## Verifying a dashboard against the DB

Use the high-level `verify_dashboard` MCP tool (or the `dashboard_verify`
playbook) — see [`docs/playbooks/dashboard-verify.md`](../playbooks/dashboard-verify.md).
It does the navigate / login / extract / compare-against-SQL flow in one
call and emits a structured diff report.

## Limits

- **Single browser, single context per call**. We don't expose long-lived
  session state to the AI on purpose — every tool call gets a fresh page.
  If you need persistence across calls (e.g., SSO cookies), set
  `context_dir` so Playwright reads/writes a stored state file.
- **Allowed origins are an absolute backstop**. The AI cannot navigate
  outside them, even if a click on a link would redirect off-site —
  Playwright's `goto` enforces, and `click` may still navigate, but
  subsequent reads will fail the allowlist check before returning to the AI.
- **Visual diffs are pixel-naive**. We compare rendered text to DB values
  semantically, not via image diff. Image diff is a roadmap item — for now,
  pair with a screenshot + manual review for visual regressions.

## What can go wrong

- **Origin not allowed**: the AI tries to navigate to a URL not in
  `allowed_origins`. → returns `policy:` error. Fix: add the origin (after
  reviewing trust).
- **Credential refused**: the AI tries a literal fill on a password field.
  → step returns `BrowserPolicyError`. Fix: pass `credential` instead.
- **Login form heuristic misses**: the high-level `login` op can't find
  user/password fields. Fix: set explicit selectors in
  `[web.credentials.<name>]`.
- **CAPTCHA / 2FA on login**: out of scope. Pair with a persistent
  `context_dir` so logins happen rarely.
