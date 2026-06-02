# Sensitivity catalog — declaring which columns are sensitive

PII detectors (regex + Presidio + local NER) catch most standard cases.
They miss what the operator KNOWS — that `user_notes` contains medical
histories, that `bank_memo` may contain routing numbers, that
`support_ticket_body` is full of customer-pasted PII. For these, the
operator declares sensitivity directly.

## Tiers (least → most strict)

| Tier | What happens to the cell |
|---|---|
| `allow` | Returned as-is. |
| `partial` | Last 4 chars visible (`***-***-1212`). |
| `synthetic` | Replaced with stable fake-but-realistic value (`Alex Rivers`). |
| `tokenize` | Replaced with stable token (`EMAIL_4f2a`). |
| `hash` | Replaced with salted hash (`[H:b3f...c91]`). |
| `redact` | Replaced with type marker (`[REDACTED:CATEGORY]`). |
| `never_leaves` | Type + length only (`<TEXT len=437>`) — the LLM never sees a byte. |

## Config

```toml
[sensitivity.warehouse-ro]
# Explicit per-column rules (most-specific glob wins).
redact = ["users.notes", "support.tickets.body", "*.address"]
hash = ["users.email", "users.phone"]
tokenize = ["orders.customer_id"]
never_leaves = ["medical.records.*", "legal.privileged.*"]

# Free-text default — applies to any TEXT / CLOB / unsized VARCHAR
# column not explicitly tagged.
free_text_default_tier = "redact"
free_text_threshold_chars = 200

# Columns explicitly EXEMPT from the free-text default.
exempt_free_text = ["public.notes.*", "marketing.posts.*"]
```

## How it interacts with PII detection

The gateway runs both detectors. When both fire on the same cell:

- **Strategy** is whichever is **stricter** (sensitivity catalog tends
  to win because it's explicit).
- **Label** comes from PII detection if it gave a specific entity type
  (`EMAIL`, `US_SSN`, `CREDIT_CARD`), otherwise from the sensitivity
  catalog (`OPERATOR_DECLARED`).

So a `email` column that the catalog marks `redact` still shows up as
`[REDACTED:EMAIL]` (PII detector knew the type), but a `notes` column
that only the free-text default catches becomes
`[REDACTED:OPERATOR_DECLARED]`.

## Why this matters

The original failure mode: an LLM profiles a `support.tickets.body`
column. Sample rows contain "called customer 415-555-1212 about her
diabetes refill." Regex catches the phone number. Presidio catches the
condition. But the average free-text column has dozens of leaks Presidio
will miss (uncommon medication names, internal IDs, unusual formatting).
The catalog defaults the whole column to `redact` so the AI never sees
the body — only `[REDACTED:OPERATOR_DECLARED]` and the (also masked)
phone number Presidio caught.

The `never_leaves` tier is for the highest-stakes columns. The LLM gets
`<TEXT len=437>` instead of any content — useful for "you can tell the
column exists and is populated, but you cannot reason about its
content."

## The local NER detector

Optional. Enable in config:

```toml
[security]
pii_detectors_enabled = ["column_name", "regex", "presidio", "local_ner"]
```

`local_ner` loads a HuggingFace transformer model (default
`dslim/bert-base-NER`, ~440MB, MIT-compatible) and runs it on every
string-typed value. Catches PERSON/ORG/LOCATION mentions inside free
text that Presidio's default heuristics miss. **Runs entirely in the
Touchstone process** — sensitive text never crosses a network boundary.

Cost: ~10-50ms per row depending on hardware. Enable for high-stakes
connections; leave off for high-QPS dev work.

Override the model with `TOUCHSTONE_NER_MODEL=acme/our-custom-ner`. The
adapter is plain HuggingFace `pipeline("token-classification")` — any
NER model with `aggregation_strategy="simple"` works.
