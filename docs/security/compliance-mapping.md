# Compliance control mapping

Touchstone is software, not a managed service, so we don't carry compliance
certifications ourselves. We provide controls that operators can map to
the frameworks they're audited against.

## SOC 2

| Control      | How Touchstone helps                                              |
| ------------ | ----------------------------------------------------------------- |
| CC6.1        | Cedar-style policy engine, per-assistant identity, per-connection scope. |
| CC6.2        | Audit log records every authentication failure (`connector/auth`). |
| CC6.3        | Default-deny; SQL allow-list; read-only by default.               |
| CC6.6        | TLS 1.3 required for remote MCP; mTLS supported.                  |
| CC6.7        | Credentials only via secret managers — never plaintext in config. |
| CC6.8        | PII masking + audit log of what was accessed.                     |
| CC7.1        | Append-only hash-chained audit log; tamper-evident.                |
| CC7.2        | Audit log retains structural query metadata for incident analysis.|
| CC7.3        | Consent gate provides change-control for sensitive ops.           |
| CC8.1        | Configuration changes go through version control + PR review.     |

## HIPAA Security Rule

| §164          | How Touchstone helps                                              |
| ------------- | ----------------------------------------------------------------- |
| .308(a)(1)(ii)(D) | Hash-chained audit log + verify-chain CLI command.            |
| .308(a)(5)(ii)(C) | Anomaly detectable via audit log analysis (CEF/JSON export).  |
| .312(a)(1)    | Per-assistant identity + per-connection RBAC via policy.          |
| .312(b)       | Audit log captures all PHI access attempts.                       |
| .312(c)(1)    | PII detection extends to PHI (Presidio MEDICAL_LICENSE, etc.).    |
| .312(d)       | Encryption in transit (TLS 1.3); encryption at rest is operator's |
|               | responsibility (host filesystem, S3 SSE, etc.).                    |
| .312(e)(1)    | All transmissions over TLS; no plaintext audit shipping.          |

## GDPR

| Article       | How Touchstone helps                                              |
| ------------- | ----------------------------------------------------------------- |
| Art. 5(1)(f)  | Data integrity and confidentiality via masking + audit.           |
| Art. 17       | Touchstone retains NO data subject data — audit holds only masked |
|               | samples and structural metadata. Right-to-erasure is satisfied at |
|               | the upstream DB; Touchstone has nothing to delete.                |
| Art. 25       | Privacy by design: default-deny, PII auto-masking.                |
| Art. 30       | Audit log doubles as a record of processing activities.           |
| Art. 32       | Pseudonymization (mask strategies: hash, tokenize, synthetic);    |
|               | encryption in transit; access control via policy engine.          |
| Art. 33       | Audit log supports breach forensics.                              |

## PCI DSS

| Req                    | How Touchstone helps                                              |
| ---------------------- | ----------------------------------------------------------------- |
| 3.4 (PAN protection)   | Credit-card detector Luhn-validates and masks PANs in results.    |
| 7.1 (need-to-know)     | Per-connection RBAC + policy engine restricts who sees what.      |
| 8.2 (strong auth)      | Secret-manager-only credentials; key-pair / OAuth for Snowflake.  |
| 10.2 (audit trails)    | Append-only audit captures all data access events.                |
| 10.5 (audit integrity) | Hash-chained log; tamper-evidence via verify-chain.               |

## What we explicitly don't claim

- We are not a HSM. Mask "synthesis" pools and hash salts are stored in
  process memory; for HSM-backed pseudonymization, integrate at the DB
  layer (Snowflake dynamic data masking + KMS).
- We are not a SIEM. Ship our logs to one (Splunk, Datadog, OTel collectors).
- We are not a DLP. We mask structured DB results, not files / chats /
  emails. Pair with a proper DLP tool for end-to-end coverage.
