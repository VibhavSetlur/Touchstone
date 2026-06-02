# Touchstone — Security & Threat Model

Touchstone gives an AI assistant the ability to issue queries against production data systems. That is genuinely scary. This document explains what we do to make it safe, what we explicitly do not protect against, and what we ask you to do.

If you find a vulnerability, please email **security@touchstone.dev** with the details. Do not open a public issue. We aim to acknowledge within 24 hours and provide a fix or mitigation within 7 days for high-severity issues.

## Threat model

### Actors

| Actor                         | Trust level | Capabilities                                                    |
| ----------------------------- | ----------- | --------------------------------------------------------------- |
| Operator                      | Trusted     | Configures connections, policies, masking strategies, audit sinks. Has DB credentials. |
| Developer / Data engineer     | Semi-trusted | Uses an AI assistant that talks to Touchstone. Has org SSO.    |
| AI assistant                  | Untrusted   | Issues MCP tool calls. May be manipulated by prompt injection. |
| Upstream LLM provider         | Untrusted   | Sees masked outputs only. Never sees raw DB credentials.       |
| External network attacker     | Untrusted   | May attempt to reach the MCP server, the GitHub App, or sniff transport. |
| Compromised dependency        | Untrusted   | A malicious PyPI / npm package pulled into the runtime.        |

The AI assistant is **explicitly untrusted**. We do not assume good intent or correct reasoning from it. Prompt injection from a malicious row in a profiled table, a crafted SQL comment, or a poisoned PR description must not be able to escalate privilege.

### Assets we protect

1. **Database credentials** — never leave the Touchstone process. Never logged. Never returned in error messages.
2. **PII inside query results** — masked before crossing the MCP boundary.
3. **Production write capability** — denied by default. Per-action allowlist, per-action consent.
4. **Audit log integrity** — append-only, signed, tamper-evident.
5. **Operator's policy decisions** — versioned, signed, reviewed on change.

### Assets we explicitly do not protect

1. **The LLM provider's training data.** If your assistant sends data upstream and the provider retains it for training, that is between you and the provider. Use a provider with zero-data-retention guarantees if this matters (Anthropic ZDR, Azure OpenAI with `data_residency`, self-hosted Llama, etc.). Touchstone masks aggressively, but cannot guarantee what the assistant's vendor does.
2. **Side channels through the assistant's prompt.** A developer can paste raw SQL output into a chat. Touchstone cannot stop a human from leaking.
3. **The DB itself.** If your Snowflake has misconfigured row-access policies, Touchstone respects them but does not add to them. Touchstone is a layer in front, not a replacement for proper DB AuthZ.

## Defenses

### 1. Default-deny at every layer

The shipped default config:

- Connections are **read-only**. Operators must explicitly mark a connection writable, and even then writes require per-statement consent.
- The SQL allow-list grammar accepts `SELECT`, `WITH`, `EXPLAIN`, and `SHOW` only. `INSERT`/`UPDATE`/`DELETE`/`DROP`/`TRUNCATE`/`CREATE`/`ALTER`/`GRANT`/`REVOKE`/`COPY` are rejected unless explicitly permitted per-connection per-action.
- Row caps: 10,000 rows per call, 1 MB per cell, 32 MB total result size.
- Query timeout: 30s server-side (set via vendor-specific session params: Postgres `statement_timeout`, Snowflake `STATEMENT_TIMEOUT_IN_SECONDS`, MySQL `MAX_EXECUTION_TIME`, etc.).
- Rate limit: 60 calls / assistant identity / minute.
- `information_schema` reads are allowed; writes are denied. `pg_catalog`, `sys.*`, `mysql.*`, `snowflake.account_usage` writes are denied.

### 2. The Trust Boundary is structural, not conventional

QA capabilities cannot import connectors directly. The single entry point is `touchstone.security.gateway.execute(...)`. A custom Ruff rule rejects PRs that violate this. An integration test asserts the invariant holds at runtime.

This means a bug in (say) the profiler cannot accidentally bypass PII masking, because the profiler has no way to reach the connector except through the gateway.

### 3. PII masking before the data crosses the MCP boundary

Every row of every result passes through the PII pipeline before being serialized into an MCP response. Strategies are per-column-per-detector configurable. The default is `[REDACTED:TYPE]` so the assistant gets type information but no values.

Operators can lower restrictions per connection for internal tooling and *raise* them for production-data connections. The shipped policy bundle treats anything tagged `prod` with maximum PII strictness.

PII detection is not perfect — see [Microsoft Presidio's accuracy notes](https://microsoft.github.io/presidio/analyzer/evaluating_analyzers/). Touchstone defaults to a low confidence threshold (0.4) to prefer false positives over false negatives. Tune with care.

### 4. Consent gates for anything irreversible

The following trigger a consent prompt before execution:

- Any non-`SELECT` statement.
- Queries against a table tagged `sensitive` or `prod`.
- Result sets > N rows (configurable, default 1,000).
- Cross-table joins that span a tagged PII boundary.
- Any new connection being used by an assistant for the first time in 24h.

Consent prompts can route to: the calling terminal, a Slack DM to the operator, a GitHub PR comment requiring approval, or a custom webhook. Default timeout: 5 minutes, after which the operation is denied.

### 5. Append-only, signed audit log

Every tool call writes one record:

```json
{
  "ts": "2026-06-02T18:42:17.301Z",
  "assistant_id": "claude-code@user@company.example",
  "assistant_session": "5f8a...",
  "tool": "query_database",
  "connection": "prod-ro",
  "sql_hash": "sha256:b3f...",
  "sql_ast_summary": {"kind": "SELECT", "tables": ["orders"], "limit": 100},
  "policy_verdict": {"verdict": "permit", "matched": "default/select-readonly"},
  "pii": {"detected": {"EMAIL": 14, "PERSON": 3}, "strategy": "redact"},
  "rows": 100,
  "cells": 1200,
  "bytes_returned": 14392,
  "latency_ms": 217,
  "sample_masked": [...first 3 rows, already masked...],
  "prev_record_hash": "sha256:a91...",
  "record_hash": "sha256:c20..."
}
```

`prev_record_hash` + `record_hash` form a tamper-evident chain. Verification: `touchstone audit verify --since 30d`. Sinks include file (default), S3 with object-lock, Splunk HEC, Datadog Logs, OTLP, and a generic webhook.

**Operators are responsible for shipping audit logs off-host promptly.** A local file can be modified by a sufficiently privileged attacker.

### 6. Credential handling

- DB credentials are loaded from environment variables, OS keychain (via [`keyring`](https://pypi.org/project/keyring/)), HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager, or Azure Key Vault. Plaintext credentials in config files are *rejected* with an error; the config field must reference a secret source.
- Credentials never appear in logs (we hook the connector layer to scrub). If a credential ever appears in `touchstone audit` output, treat it as a P0 bug.
- Per-connection ephemeral credentials are supported where the DB offers them: Snowflake key-pair auth, BigQuery service-account impersonation, RDS IAM auth.

### 7. Transport security

- **Local (stdio)** — MCP runs over stdin/stdout of a process the operator spawned. The kernel boundary is the trust boundary.
- **Remote (Streamable HTTP)** — TLS 1.3 required. Auth via OAuth 2.0 with [PKCE](https://datatracker.ietf.org/doc/html/rfc7636) (recommended: Authelia, Authentik, Auth0, Okta, Azure AD). Optional mTLS for service-to-service. No anonymous access.
- **GitHub App** — webhook signatures verified per GitHub's spec. Installation tokens are cached in-memory only, refreshed per-call.

### 8. Supply chain

- All Python deps are pinned with hashes in `uv.lock`. Renovate keeps them current; security updates auto-merge after CI passes.
- All npm deps use `pnpm` with `pnpm-lock.yaml` and `--frozen-lockfile` in CI.
- SBOMs are generated per release ([CycloneDX](https://cyclonedx.org/)).
- Releases are signed with [Sigstore](https://www.sigstore.dev/) cosign.
- We have a SLSA build-provenance attestation on every published artifact ([SLSA Level 3](https://slsa.dev/spec/v1.0/levels)).

### 9. Prompt-injection resistance

Touchstone treats every byte of data returned from a database as untrusted input that may contain instructions to the LLM. We do not "interpret" data — we serialize it and return it. The MCP tool descriptions are the only thing telling the assistant what tools exist; we never instruct the assistant via tool result content.

This is necessary but not sufficient. A determined prompt injection in a database row could still mislead the assistant into asking for a follow-up tool call. The consent gates and rate limits are the backstop.

## What we ask of operators

Touchstone is a defense in depth, not a substitute for fundamentals. Please:

1. **Use a dedicated read-only DB role for Touchstone.** Do not give it your superuser credentials. Apply row-level security in the DB itself.
2. **Tag your sensitive tables.** Touchstone treats tagged tables strictly; untagged tables fall back to whichever default you set, which may be more permissive than you want.
3. **Ship audit logs off-box** to an immutable sink. Set a retention you'd be comfortable defending to a regulator.
4. **Review the default Cedar policy bundle.** It is conservative, but your environment has specifics we cannot anticipate. Customize and check policies into version control.
5. **Rotate credentials.** Touchstone honors short-lived credentials; please use them.
6. **Subscribe to security advisories.** GitHub watch → `Custom > Security advisories`, or our [security mailing list](mailto:security-announce@touchstone.dev).

## Compliance posture

Touchstone itself is software, not a service, so we don't carry compliance certifications. We provide the controls that make it possible for operators to operate Touchstone in a compliant environment:

- **SOC 2 CC6 (Logical access)** — Cedar policy engine, per-assistant identity, audit log.
- **SOC 2 CC7 (System operations)** — append-only signed audit log, SBOM, SLSA attestations.
- **GDPR Art. 32 / HIPAA §164.312** — encryption in transit, encryption at rest (operator-configured), audit trail, access control.
- **GDPR Art. 17 (Right to erasure)** — Touchstone retains no DB data; audit logs retain only masked samples and structural metadata.
- **PCI DSS 3.4** — credit card detection + masking is enabled by default with Luhn validation.

Map of Touchstone controls → control frameworks lives in [`docs/security/compliance-mapping.md`](docs/security/compliance-mapping.md).

## Reporting a vulnerability

Email **security@touchstone.dev** (PGP key in [`docs/security/pgp-key.asc`](docs/security/pgp-key.asc)). Please include:

- A description of the vulnerability.
- Steps to reproduce.
- Affected version(s).
- Your assessment of severity.
- Whether you have disclosed (or plan to disclose) elsewhere.

We commit to:

- Acknowledging receipt within 24 hours.
- A first substantive response within 72 hours.
- A fix or mitigation for high-severity issues within 7 days.
- Public disclosure coordination — we will not name you without consent.

Out of scope (please don't report):

- Findings that require an operator to have already misconfigured policies.
- Findings against unsupported deployment topologies (e.g., the MCP server exposed to the public internet without an auth proxy).
- Findings against pre-1.0 alpha components (the Triage UI, the GitHub App) — we appreciate the heads-up, but they're not yet eligible for SLA.
