# Changelog

All notable changes to Touchstone. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
semantics: [SemVer](https://semver.org/spec/v2.0.0.html). Pre-1.0 — minor
versions may break APIs; we list breaking changes prominently.

## [Unreleased]

### Added
- One-line installer `install.sh` (works with `uv` / `pipx` / `pip`).
- `Makefile` with `install`, `dev`, `test`, `doctor`, `compose-up`,
  `verify`, `build`, `smoke`.
- `touchstone --version` flag.
- `quickstart` extras bundle (`postgres`, `duckdb`, `web`).
- End-to-end smoke-test script (`scripts/smoke_test.py`).
- `GETTING-STARTED.md` — a real 5-minute path.
- Verified MCP client config snippets for Claude Code, Cursor, Continue,
  VS Code GitHub Copilot.

### Changed
- Install docs corrected: removed misleading `pipx install touchstone-mcp`
  references. Touchstone is git-installed today; PyPI release planned.
- `touchstone-core` dependencies trimmed to the minimum imported at the
  top level. Heavy/optional deps (cryptography, presidio, transformers,
  driver libs) moved to extras.
- `web` extra now includes `cryptography` (needed for session encryption).
- New `pii-local-ner` extra (`transformers` + `torch`) for the in-process
  NER detector.

### Fixed
- `policy.py` missing `import ast` at module scope — would crash at import.
- `PIIDetector` / `Masker` `@dataclass(slots=True)` collisions with lazy
  `__post_init__` attribute assignment.
- Verifier `_compare("10.5K", 10500.56, 0.01)` was returning "match within
  tolerance" instead of `rounded_truncation`; now always flags
  suffix-rounded values.
- Test fixture used `read_only=False` for a "read-only test" connection;
  fixed to `read_only=True` so policy default-allow rule applies.

## [0.1.0] — 2026-06-02

Initial scaffold + hardening pass.

### Added — initial scaffold
- `touchstone-core`: connectors for 11 engines (Postgres, MySQL, Snowflake,
  BigQuery, Mongo, Databricks, Redshift, Trino, ClickHouse, DuckDB,
  SQLite) + flat-file adapter (CSV / Excel / Parquet / JSON / NDJSON).
- Security primitives: gateway, policy engine, PII detector
  (column-name + regex + Presidio), masker, consent gate, append-only
  hash-chained audit log, rate limiter.
- QA capabilities: profiler, env-differ, validator, lineage,
  PR-impact analyzer, test-gen.
- Web automation: Playwright wrapper, dashboard verifier.
- Knowledge store (SQLite-backed: notes, owners, tasks, decisions, PR digest).
- GitHub intel: blame, CODEOWNERS, recent-activity helpers.
- LLM adapter: Anthropic / OpenAI / Azure / Bedrock / Vertex / Ollama.
- Playbooks: migration_safety, dashboard_verify, anomaly_triage,
  pre_release_check, data_handoff.
- Notifications: Slack / Teams / Email senders (channel-name dispatch).
- `touchstone-mcp`: MCP server exposing ~30 tools through the gateway.
- `touchstone-cli`: CLI mirroring every capability.
- `touchstone-github`: Probot GitHub App scaffold.
- `touchstone-ui`: Next.js Triage UI scaffold.
- Docker compose dev stack + per-surface Dockerfiles.
- CI workflows (lint, type-check, unit, integration, CodeQL).

### Added — hardening pass (response to architectural review)
- **MFA-aware browser sessions** (`web/session_store.py`,
  `web/bootstrap.py`): AES-GCM-256 encrypted Playwright `storage_state`
  + `touchstone session bootstrap` flow + mid-session expiry detection.
- **Sensitivity catalog** (`security/sensitivity.py`): operator-declared
  per-column tiers including a `never_leaves` tier that returns only
  `<TEXT len=N>` to the LLM.
- **Local NER detector** (`security/pii.py`): HuggingFace transformer in
  process — catches free-text PII Presidio misses, no external API call.
- **Cost guard** (`security/cost_guard.py`): static SQL guard
  (cross-join refusal, auto-LIMIT), EXPLAIN preflight (including
  BigQuery free dry-run), per-assistant concurrency cap.
- **Per-tenant isolation** (`security/tenant.py`): `ConnectorPool` keyed
  by `(tenant_id, connection_name)`; manifest filters per-tenant
  connection access; identity-from-MCP-transport via contextvar.
- **Snapshot transactions** (`security/snapshot.py`): per-engine
  isolation (Postgres REPEATABLE READ, MySQL CONSISTENT SNAPSHOT,
  Snowflake AT(TIMESTAMP), BigQuery FOR SYSTEM_TIME, Databricks
  TIMESTAMP AS OF) + sqlglot rewriting + `gateway.snapshot()` context.
- **Knowledge-store content guard** (`knowledge/store.py`): refuses
  notes with >=5 emails / >=5 phones / >=3 Luhn-valid cards / >16KiB
  body. Defense against future vector-search poisoning.
- **Dynamic dashboard handling** (`web/waiters.py`, `web/exporters.py`):
  `wait_for_stable_row_count`, `canvas_is_present` flag, BI-native CSV
  exporters (Looker tile, Tableau view, Grafana panel, Metabase card).
- **Audit log rotation** (`security/audit.py`): size-based with
  `verify_rotated_chain` walking across files.
- **`touchstone doctor`** (`diagnostics.py`): end-to-end self-diagnosis.
- **`touchstone session bootstrap`** CLI command + `session list` /
  `session delete`.
- **Trust-boundary AST test**: enforces no QA code imports connectors
  directly.
