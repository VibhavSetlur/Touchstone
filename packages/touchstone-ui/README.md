# @touchstone/ui

The Triage UI — a Next.js app where a human reviews findings flagged by
Touchstone (PII detections, policy denials, consent requests, audit-log
anomalies). Decisions feed back into the policy engine.

## Status

Alpha. The shell, the audit-log explorer, and the consent-prompt UI work.
The policy-rule editor and the lineage explorer are stubs.

## Quickstart

```bash
pnpm install
TOUCHSTONE_AUDIT_FILE=~/.touchstone/audit.jsonl pnpm dev
```

Open http://localhost:3001.

## Architecture

- Server-side reads of the audit JSONL file (or remote sink) via Next.js
  Server Actions.
- Client-side filtering / search with SWR.
- Consent-prompt webhook lands here in dev; in prod, a webhook proxy
  forwards Touchstone consent requests so the human can approve/deny.

The UI is **read-only by default**. Approving a consent prompt or saving a
policy rule requires a step-up authentication challenge (configurable: WebAuthn,
TOTP, or operator-supplied JWT claim).
