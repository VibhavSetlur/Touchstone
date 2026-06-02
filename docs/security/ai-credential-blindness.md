# How the AI assistant stays blind to your credentials

This is the single most important security property of Touchstone. If it's
ever violated, that is a P0 bug — please file as such.

## The contract

The AI assistant calling Touchstone (via MCP) sees:

- **Names**: connection names (`prod-ro`), credential references
  (`looker_admin`), notification channel names (`security-on-call`).
- **Schema metadata**: table names, column names, types.
- **Masked data**: query results with PII redacted/hashed/tokenized.
- **Audit summaries**: counts of PII categories detected, not the values.
- **Tool errors**: with sensitive substrings scrubbed.

The AI assistant NEVER sees:

- **DB credentials**: passwords, API keys, key-pair private keys, OAuth tokens.
- **Webhook URLs**: Slack, Teams, email SMTP credentials.
- **Web logins**: usernames and passwords used by the browser automation.
- **LLM provider keys**: Anthropic/OpenAI/Azure/Bedrock/Vertex API keys.
- **Raw PII values**: anything matched by the detector pipeline before
  masking.

## How this is enforced

### 1. Config-level: only references, no plaintext

Every secret in `touchstone.toml` uses a URI scheme — `env://`, `keyring://`,
`vault://`, `awssm://`, `gcpsm://`, `azurekv://`. Config that contains a raw
password is **rejected at load time** with a `ConfigError`.

```toml
# REJECTED
password = "hunter2"

# OK
password_ref = "env://POSTGRES_PASSWORD"
```

The MCP server, the CLI, and the GitHub App all share this loader. There is
no separate path that accepts plaintext.

### 2. Runtime-level: resolution happens inside the trust boundary

Secrets are resolved in `touchstone.security.gateway.execute()` and
`touchstone.web.browser.BrowserSession._resolve_credential()` — after the
AI's tool call has been authorized, just before it touches the underlying
driver. The resolved value lives in a local variable, is consumed
immediately, and is `del`-ed.

### 3. MCP-level: tool inputs are names, not values

MCP tools that touch credentials take `connection: str`,
`credential: str`, `channel: str`, etc. — never `password: str`,
`webhook_url: str`, `api_key: str`. The schema literally does not allow the
AI to pass a secret.

### 4. Browser-level: refuse literal fills on password fields

The `BrowserSession.fill()` op inspects the selector. If it looks like a
credential field (`input[type=password]`, `[name*=password]`,
`[autocomplete=current-password]`, etc.) and the AI passed a literal `value`
instead of a `credential` reference, the session **refuses** the step with
a `BrowserPolicyError`. The audit log gets a security event.

### 5. Output-level: scrubbing on the way out

Connectors scrub `password=`, `pwd=`, `token=`, and `user:pass@host` patterns
from error messages before re-raising. The browser session scrubs any
credential value it typed if the page echoes it back in `extract_text`.
PII masking happens to every cell of every result before serialization to
MCP.

### 6. Notification-level: channel names, not URLs

The AI calls `notify("data-team-alerts", "...")` — not
`notify("https://hooks.slack.com/...", "...")`. The Notifier resolves the
channel name to a webhook (or SMTP target) inside the trust boundary. An AI
that "knows" a Slack webhook URL has no way to use it — there is no MCP tool
that accepts a URL.

### 7. LLM-provider-level: Touchstone calls its own LLM, not yours

The LLM adapter (used internally for test-gen) holds the operator's API key.
It is not exposed via MCP. The calling AI assistant has no way to reach
Touchstone's LLM directly, and Touchstone's LLM has no way to call back into
the MCP.

## What this does NOT protect against

- **The operator pasting a secret into the AI's chat manually.** Touchstone
  cannot stop humans from leaking; only enforce that the system doesn't.
- **The upstream LLM provider's data-retention policy.** Use a provider with
  zero-retention guarantees if outputs (post-masking) are sensitive.
- **A misconfigured DB that grants the touchstone role write or admin
  privileges.** Run touchstone as a dedicated read-only role at the DB
  level.

## How to verify it

```bash
# 1. Confirm no plaintext secrets in your config.
touchstone connections   # any plaintext password would have failed load

# 2. Tail the audit log and confirm no credential strings appear.
touchstone audit tail -n 100 | grep -iE 'password|token|secret|api_key'
# expected: zero matches against actual secrets (you'll see scrubbed forms)

# 3. Run the security test suite.
uv run pytest -m security -v
# Asserts the trust boundary holds.
```

If you find a way to make the AI see a credential, please email
**security@touchstone.dev** before disclosing publicly.
