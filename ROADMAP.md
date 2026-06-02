# Roadmap

This is what we think we're building, in rough priority order. Community
drives priority — file an issue or PR to vote.

## Now

- **Streaming masker.** Today, results materialize before redaction. Big
  Snowflake queries OOM. Row-iterator masker + backpressure-aware MCP
  transport.
- **Snowflake hardening.** OAuth ⏳, session tags for cross-system audit
  correlation ⏳, warehouse cost guardrails ⏳.
- **dbt manifest deeper integration.** `pr.impact` auto-detects
  `target/manifest.json` and uses it for lineage. Today the user has to
  pass it explicitly.
- **Browser session persistence.** Save & reuse Playwright `storage_state`
  per-credential so SSO logins survive across runs.
- **Per-channel consent gate on `notify`.** "send to #all-hands" should
  prompt the operator, not auto-send.

## Next (Q3 2026)

- **Cross-engine value diff** (Postgres ↔ Snowflake migrations).
- **Slack consent gate** (native Block Kit, not the generic webhook).
- **Triage UI feedback loop**: human approves/denies → policy learns.
- **GitLab + Bitbucket** apps.
- **PII detector marketplace**: finance (FINRA), healthcare (HIPAA
  categories), EU (GDPR special categories).
- **More playbooks**: `recovery_rehearsal`, `customer_data_request`,
  `quarter_close_signoff`, `vendor_data_audit`.
- **Vector-backed knowledge search** (`knowledge.vector`): semantic notes
  search via `sentence-transformers` or operator-chosen embedding model.

## Later (2026)

- **Lineage at scale.** Per-dialect extensions for Snowflake
  `MATCH_RECOGNIZE`, BigQuery `ML.PREDICT`, Redshift `UNLOAD`.
- **Cost guardrails.** Each connector reports estimated bytes scanned;
  policy can deny / charge to a budget.
- **Test-gen v2.** Mine production query logs to discover invariants the
  data already satisfies, propose those as expectations.
- **Multi-tenant control plane (optional OSS).** Shared policy bundles,
  cross-team audit aggregation, RBAC for operators.
- **OpenLineage emit.** Touchstone events as OpenLineage facets, so
  Marquez / Datahub pick them up.
- **Visual-diff for dashboards.** Pixel + layout diff complementing the
  current semantic-value diff.
- **MCPB (DXT) packaging** — one-click install for Claude Desktop.
- **Local LLM-as-policy-judge** for the trickier "should this query run?"
  questions where Cedar-style rules are too coarse.

## Considered, deferred

- **Vector DB connectors** (Pinecone, Weaviate, pgvector). Useful, but
  semantics differ enough from SQL that we want to ship the SQL story
  first.
- **Realtime data observability** (Kafka, CDC). Out of scope — that's
  what Monte Carlo / Acceldata / Bigeye do.
- **A managed SaaS.** Maybe later. The OSS is the product.

## Won't do

- **An LLM of our own.** We use yours.
- **Bypass-able policies.** No "advanced mode" that turns off the trust
  boundary. If you need a tool to talk to your DB without masking, write
  your own.
- **Surfaces that hand the AI raw credentials.** That property is what
  makes Touchstone useful in regulated environments.
