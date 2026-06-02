# Architectural vulnerabilities — and what we did about them

A reviewer raised seven serious architectural concerns about an earlier
version of Touchstone. They were right. This doc enumerates each, the
specific code that addresses it, and the test that proves it.

## 1. MFA / SSO breaks headless automation

**Risk:** Looker / Tableau / Grafana / internal apps are almost always
behind Okta, Duo, Ping. A headless browser hitting a Duo push hangs
forever or crashes. Without a fix, the whole web-automation path is
useless in any enterprise.

**Fix:** Interactive `touchstone session bootstrap` flow. Operator opens a
**headed** browser once per credential, completes login (including MFA),
and Touchstone captures Playwright `storage_state` (cookies +
localStorage), **encrypts it with AES-GCM-256** (key from
`TOUCHSTONE_SESSION_KEY`, PBKDF2 200k iterations, per-credential salt), and
stores it under `~/.touchstone/web-sessions/<cred>.aesgcm`. Headless calls
load the encrypted state; the AI never sees the cookies, never sees the
key, never sees the credentials.

**Mid-session expiry detection:** The browser checks every step's
post-action URL/title for login-page indicators (`/login`, `/signin`,
Okta, `sign in`) and MFA-challenge indicators (`duo`, `verify your
identity`, `2-step`). On a hit, the session is marked **expired**, the
step returns `SessionExpiredError`, and the operator is pointed at
re-bootstrap. The AI cannot route around this — there's no MCP tool to
"force-continue".

**Files:** [`web/session_store.py`](../../packages/touchstone-core/src/touchstone/web/session_store.py),
[`web/bootstrap.py`](../../packages/touchstone-core/src/touchstone/web/bootstrap.py),
[`web/browser.py`](../../packages/touchstone-core/src/touchstone/web/browser.py)
`(SessionExpiredError, _check_expiry)`.

**Tests:** [`tests/unit/test_session_store.py`](../../tests/unit/test_session_store.py) — encryption roundtrip, key isolation, login/MFA detection.

## 2. PII detection blindspots in unstructured fields

**Risk:** Presidio + regex catch standard cases (email, SSN, card) but
miss `user_notes` containing free-form medical histories, `support_ticket_body`
containing arbitrary PII pasted by customers, `bank_memo` text fields. If
unmasked, that compliance-sensitive data is shipped to the external LLM
provider on the next tool call. Regulatory violation.

**Fix:** Three additive layers, all enforced inside the gateway:

1. **Operator-declared sensitivity catalog** ([`security/sensitivity.py`](../../packages/touchstone-core/src/touchstone/security/sensitivity.py)).
   Per-connection `[sensitivity.<conn>]` config maps glob patterns to
   tiers: `redact` / `hash` / `tokenize` / `partial` / `synthetic` /
   `never_leaves`. Most-specific glob wins.
2. **Free-text default tier.** Any column typed `TEXT` / `CLOB` / `BLOB` /
   `VARCHAR(>=255)` / `VARCHAR()` without length is treated as free-text
   and masked at `redact` by default. Operators opt out per-pattern via
   `exempt_free_text`.
3. **`never_leaves` tier.** New strongest tier — the cell is returned as a
   type-only descriptor (`<TEXT len=437>`). The LLM knows the column
   exists and has data, but never sees a byte. Use for medical records and
   any other "if this leaks we're done" column.
4. **Optional local NER detector** ([`pii.py`](../../packages/touchstone-core/src/touchstone/security/pii.py) `LocalNERDetector`).
   Runs a HuggingFace transformer model in-process (default
   `dslim/bert-base-NER`) for PERSON/ORG/LOCATION detection in
   unstructured text. **Never calls an external API** — sensitive data
   doesn't leave the host.

In the gateway, the sensitivity tier wins on **strategy** (operator
declaration > heuristic), but the PII detector wins on **label** (more
specific entity type). So an `email` column flagged as both stays
`[REDACTED:EMAIL]` while a `notes` column flagged only by free-text
heuristic becomes `[REDACTED:OPERATOR_DECLARED]`.

**Tests:** [`tests/unit/test_sensitivity.py`](../../tests/unit/test_sensitivity.py),
[`tests/unit/test_gateway_e2e.py`](../../tests/unit/test_gateway_e2e.py) (asserts email column stays
labeled EMAIL even when sensitivity catalog applies).

## 3. Read-only queries can still DoS the warehouse

**Risk:** `SELECT COUNT(DISTINCT user_id) FROM events_5b_rows` is "read
only" but takes a Snowflake warehouse to its knees. An LLM in a retry
loop can spawn 50 of these in parallel.

**Fix:** Three guards in the cost-guard pipeline
([`security/cost_guard.py`](../../packages/touchstone-core/src/touchstone/security/cost_guard.py)),
all enforced before the connector is touched:

1. **Static SQL guard** via `sqlglot`:
   - Refuses cross-joins (explicit CROSS JOIN, or JOIN without ON/USING).
   - Refuses SELECT * without LIMIT on tables tagged `large`.
   - Auto-injects `LIMIT N` (default 10,000) when an unbounded
     non-aggregating SELECT is detected. The rewrite shows up in the
     returned `warnings` so the AI knows.
2. **EXPLAIN preflight.** Engine-specific:
   - Postgres / Redshift: `EXPLAIN (FORMAT JSON)` → estimated rows.
   - MySQL: `EXPLAIN FORMAT=JSON` → walked for `rows_examined_per_scan`.
   - **BigQuery: dry-run job** → exact bytes processed before the
     real query ever runs.
   - Snowflake: `EXPLAIN USING TABULAR` (best-effort).
   - DuckDB: `EXPLAIN` for plan inspection.
   Rejected when estimate > `max_estimated_rows` (100M default) or >
   `max_estimated_bytes` (10 GB default).
3. **Per-assistant concurrency cap** (default 4 in-flight queries). A
   panicked AI cannot spawn 50 parallel deep scans.

Plus the existing per-connection `timeout_seconds`, `row_cap`, `byte_cap`.

**Tests:** [`tests/unit/test_cost_guard.py`](../../tests/unit/test_cost_guard.py).

## 4. Multi-tenant connection-pool cross-pollination

**Risk:** A single MCP server serving multiple teams/clients could leak
connection handles, schema caches, or session state between tenants.
Tenant B's queries hit Tenant A's connection.

**Fix:** Structural isolation
([`security/tenant.py`](../../packages/touchstone-core/src/touchstone/security/tenant.py),
[`security/gateway.py`](../../packages/touchstone-core/src/touchstone/security/gateway.py)):

- **`tenant_id` in every `ToolCallContext`**. Resolved from the MCP
  transport's authenticated identity (OAuth subject claim, mTLS CN,
  contextvar set per-request); never from anything the AI controls.
- **`ConnectorPool` keyed by `(tenant_id, connection_name)`**. Tenant A
  and Tenant B accessing the same `prod-ro` connection get **distinct
  connector instances**, distinct driver sessions, distinct in-memory
  state. No sharing path exists in code.
- **Tenant-scoped manifest.** `[tenants.<id>]` config lists which
  connections that tenant can address. Cross-tenant access raises
  `PolicyDeniedError` before any connector is touched.
- **Audit records carry `tenant_id`**. The hash chain breaks if tenant
  context is tampered with.
- **Per-tenant LRU eviction.** Bounded pool size per tenant prevents one
  tenant from exhausting capacity.

**Tests:** [`tests/unit/test_tenant_isolation.py`](../../tests/unit/test_tenant_isolation.py).

## 5. TOCTOU between profile and query execution

**Risk:** AI profiles a table at T0, generates a SQL based on that schema
at T1, executes at T2. A parallel dbt run between T0 and T2 alters the
schema. The AI's "carefully reasoned" query runs against a different
reality.

**Fix:** First-class **snapshot transactions**
([`security/snapshot.py`](../../packages/touchstone-core/src/touchstone/security/snapshot.py)).
Engine-specific isolation:

- **Postgres**: `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`.
- **MySQL**: `START TRANSACTION WITH CONSISTENT SNAPSHOT`.
- **DuckDB / SQLite**: `BEGIN READ ONLY` / `BEGIN IMMEDIATE`.
- **Snowflake**: capture timestamp at entry, rewrite every subsequent
  query with `AT(TIMESTAMP => '...')` (Time Travel).
- **BigQuery**: append `FOR SYSTEM_TIME AS OF TIMESTAMP('...')`.
- **Databricks**: append `TIMESTAMP AS OF '...'` (Delta).
- **Other engines**: best-effort timestamp recording so downstream reports
  flag staleness.

The MCP `snapshot_begin` / `snapshot_end` tools let the AI explicitly
pin a snapshot for a sequence of operations. The Python API exposes a
`gateway.snapshot()` context manager. The snapshot timestamp is returned
in every result inside the snapshot so the AI can reason about
when the data was captured.

**Tests:** [`tests/unit/test_snapshot_rewrite.py`](../../tests/unit/test_snapshot_rewrite.py).

## 6. Vector embedding poisoning via PII scrubbing

**Risk:** If Touchstone ever builds a vector index over warehouse data,
the embeddings carry semantic information that can be partially
reconstructed back to original text — even after string-level PII
masking. The vector store becomes a poisoned PII sink an attacker can
mine.

**Fix:** Two-part policy:

1. **Touchstone does not build vector indexes over warehouse data.** Full
   stop. The knowledge store
   ([`knowledge/store.py`](../../packages/touchstone-core/src/touchstone/knowledge/store.py))
   holds **metadata only** (notes, owners, tasks, decisions, PR digests).
   It explicitly refuses content that looks like data dumps (>= 5 emails,
   >= 5 phones, >= 3 Luhn-validated cards, body > 16 KiB) via
   `_guard_content`. The guard runs at every write.
2. **If we ever add semantic search** (planned `knowledge/vector.py`), it
   will operate only over aggregates/profiles/notes — never over raw
   row values — and only after mask-then-embed enforcement at the layer
   boundary. The roadmap doc spells out the order-of-operations.

**Tests:** [`tests/unit/test_knowledge_guard.py`](../../tests/unit/test_knowledge_guard.py) — rejects PII dumps,
multiple cards, oversize bodies.

## 7. Dynamic dashboards: lazy load, virtualized tables, canvas charts

**Risk:** Modern BI tools (Looker, Tableau, Grafana) render skeletons
first, hydrate on scroll. Tables are virtualized — only visible rows are
in the DOM. Charts are canvas. A naive `extract_table` returns the
skeleton; a naive VLM-on-canvas misses tooltips. False-negative QA.

**Fix:** Three additive techniques
([`web/waiters.py`](../../packages/touchstone-core/src/touchstone/web/waiters.py),
[`web/exporters.py`](../../packages/touchstone-core/src/touchstone/web/exporters.py)):

1. **Smart waiters.** `wait_for_stable_row_count` polls and scrolls the
   container until the row count stops growing for N iterations. The
   `extract_table` step calls this automatically before reading the DOM.
2. **`canvas_is_present` flag.** Every `BrowserStepResult` carries
   `canvas_detected`. The AI sees that DOM scraping will be incomplete
   and knows to switch to the data export path.
3. **BI-native exporters** — the recommended path.
   `bi_export` step uses the BI tool's own CSV/JSON download endpoint,
   authenticated by the same Playwright cookies, no DOM scraping
   needed. Bundled exporters: Looker tile CSV, Tableau view CSV, Grafana
   panel CSV, Metabase card CSV. Returns rows in the same shape as
   `extract_table`, so the verifier code doesn't care which path was used.

This means the typical Looker verification looks like:

```json
[
  {"op": "navigate", "url": "https://looker.acme.com/dashboards/42"},
  {"op": "bi_export", "metadata": {
      "exporter": "looker_tile_csv",
      "args": {"dashboard_url": "https://looker.acme.com/dashboards/42",
               "element_id": 7}
  }}
]
```

Bypassing the DOM entirely.

---

## Test summary

```
$ pytest tests/unit tests/security
.....................................................................
69 passed
```

All 7 vulnerabilities have at least one dedicated test that fails without
the fix. The trust-boundary tests (AST-scan that no QA code imports
connectors directly) also pass. CI runs these on every PR.
