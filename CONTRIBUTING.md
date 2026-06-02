# Contributing to Touchstone

Thanks for considering it. Touchstone is small enough that thoughtful contributions move the needle fast.

## Easiest entry points

1. **A new connector.** ~150 lines + tests. See [`docs/connectors/adding-a-connector.md`](docs/connectors/adding-a-connector.md). High-value targets we don't have yet: Vertica, SAP HANA, Athena, Firebolt, MotherDuck-hosted DuckDB, IBM Db2.
2. **A new PII detector.** Pattern-match plus a confidence function. See [`docs/security/adding-a-detector.md`](docs/security/adding-a-detector.md). Especially useful: country-specific national-ID detectors (we ship US SSN and EU IBAN; everything else is community).
3. **A new policy rule.** Cedar policy + a test. See [`docs/security/writing-policies.md`](docs/security/writing-policies.md).

## Development setup

```bash
git clone https://github.com/touchstone-dev/touchstone
cd touchstone

# Python: uv workspace
uv sync --all-packages
uv run pytest

# TypeScript: pnpm workspace
pnpm install
pnpm -r test

# Bring up the dev stack (Postgres, MySQL, Mongo, MinIO, etc.)
docker compose up -d

# Run the MCP server against the dev stack
uv run touchstone-mcp --config examples/postgres-quickstart/touchstone.toml
```

## Coding style

- **Python:** Ruff (linter + formatter, configured in `pyproject.toml`). Pyright strict mode. `pytest` for tests.
- **TypeScript:** Biome. Strict TS. Vitest.
- **Commits:** Conventional Commits. Squash on merge.

## The Trust Boundary

QA capabilities cannot import connectors directly. A custom Ruff rule enforces this; an integration test verifies it at runtime. If you find yourself wanting to import `touchstone.connectors` from `touchstone.qa`, stop — you're working around the design. Ask first.

## Tests

- Unit tests for every QA capability against DuckDB (fast, in-process).
- Integration tests for connectors against real DBs via `docker compose` profiles.
- A security test suite asserts the trust boundary holds.
- PRs that add a feature must add tests; PRs that fix a bug must add a regression test.

## License

Apache 2.0. By submitting a PR you certify the [DCO](https://developercertificate.org/) — a `Signed-off-by` line on each commit.
