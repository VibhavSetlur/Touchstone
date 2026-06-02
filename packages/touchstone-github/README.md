# @touchstone/github

A [Probot](https://probot.github.io/)-based GitHub App that runs Touchstone's
PR-impact analysis when a pull request touches SQL or dbt models, and posts
the report as a structured PR comment + check run.

The app does **not** see your database — it spawns a `touchstone-cli` process
in your environment (CI runner, self-hosted runner, or VPC sidecar) and
forwards the report.

## Architecture

```
[GitHub PR event] ──webhook──► [Probot app]
                                      │
                                      ▼
                            [spawn touchstone CLI]
                                      │
                              (in customer env)
                                      │
                                      ▼
                       [PR comment + check run]
```

## Quickstart (development)

```bash
pnpm install
cp .env.example .env       # fill in APP_ID, PRIVATE_KEY_PATH, WEBHOOK_SECRET
pnpm dev
```

Use [smee.io](https://smee.io/) to proxy webhooks during local dev.

## Production

- Deploy as a container; one instance per GitHub org is sufficient.
- The Touchstone CLI must be reachable from the runner — either bundled into
  the same image (`docker/touchstone-github.Dockerfile`) or available via a
  remote MCP gateway.
- Configure repository overrides via `.touchstone.yml` in each repo root.

## Status

Alpha. The webhook handlers and report formatter work; the consent-back-into-
GitHub flow (approve a sensitive op via PR comment reaction) is a roadmap
item.
