# dbt integration

Touchstone is dbt-aware in two places:

1. **`pr-impact` parses dbt manifests** — when you provide `--dbt-manifest`
   to `touchstone pr`, downstream consumers of changed columns are enumerated
   via dbt's `child_map`.
2. **`lineage` chases through dbt models** — the lineage walker reads the
   manifest to give you intra-model lineage even when individual models
   reference each other only through `{{ ref(...) }}`.

## Wiring it up

In your dbt project's `dbt_project.yml`:

```yaml
# nothing dbt-side required — Touchstone just reads target/manifest.json
```

In your CI:

```yaml
- run: dbt parse
- run: touchstone pr \
    --repo ${{ github.repository }} \
    --pr ${{ github.event.pull_request.number }} \
    --dialect snowflake \
    --dbt-manifest target/manifest.json
```

If you use the Touchstone GitHub App, the bot picks up `target/manifest.json`
automatically when present in the repo.

## What it does not (yet) do

- Run dbt models in a sandbox. SQLMesh and dbt-cloud already do this well;
  we may add a "run the PR's models against a sandbox" mode later, but it's
  not a v0.1 feature.
- Translate dbt test failures into Touchstone validator results. Roadmap.
