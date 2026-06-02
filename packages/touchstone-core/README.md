# touchstone-core

The Touchstone core: connectors, security primitives (gateway, policy,
PII detection, masking, audit, consent, cost guard, sensitivity catalog,
tenant isolation, snapshot transactions), QA capabilities (profile, diff,
validate, lineage, PR impact, test generation), web automation
(Playwright + encrypted session store), knowledge store, GitHub intel,
LLM adapter, playbooks, notifications.

This is the library — for the CLI install [`touchstone-cli`](../touchstone-cli/),
for the MCP server install [`touchstone-mcp`](../touchstone-mcp/), or use
this directly from Python.

## Install

```bash
# Quickstart bundle (Postgres + DuckDB + web automation):
pip install -e 'packages/touchstone-core[quickstart]'

# Everything:
pip install -e 'packages/touchstone-core[all]'

# One driver at a time:
pip install -e 'packages/touchstone-core[snowflake]'
pip install -e 'packages/touchstone-core[bigquery,mongodb]'
```

See the full project at https://github.com/VibhavSetlur/Touchstone.

## License

Apache-2.0.
