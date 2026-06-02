# GitHub integration

Touchstone integrates with GitHub two ways:

1. **The GitHub App** (`touchstone-github`) — posts PR comments + check runs
   automatically when a PR touches SQL or dbt models.
2. **The CLI** (`touchstone pr`) — fetches a PR's diff and emits a report,
   useful in CI runners without installing the app.

## Option A: The GitHub App

### When to use it

- You want PR comments to appear automatically, without each repo wiring up
  CI.
- You're OK running a small service (one container) on your infra.

### Setup

1. Create a GitHub App: https://github.com/settings/apps/new
   - **Permissions**:
     - Repository → Pull requests: Read & write
     - Repository → Checks: Read & write
     - Repository → Contents: Read-only
     - Repository → Metadata: Read-only
   - **Subscribe to events**: `Pull request`
   - **Where can this GitHub App be installed?** Only your org (or wider, if
     you want).

2. Generate a private key. Save it.

3. Deploy the app:

```bash
docker run -d \
  -e APP_ID=123456 \
  -e PRIVATE_KEY="$(cat private-key.pem)" \
  -e WEBHOOK_SECRET=changeme \
  -e TOUCHSTONE_CLI=touchstone \
  -p 3000:3000 \
  ghcr.io/touchstone-dev/touchstone-github:latest
```

4. Point the App's webhook URL at the deployed instance.

5. Install the App to the repositories you want covered.

### Per-repo configuration

Drop `.touchstone.yml` in the repo root:

```yaml
dialect: snowflake
dbt_manifest: target/manifest.json
include_paths: ["models/**/*.sql", "migrations/**/*.sql"]
exclude_paths: ["models/scratch/**"]
checks:
  fail_on_drop: true
  fail_on_retype: false
```

## Option B: The CLI in CI

If you'd rather not run a service, drop this into your CI:

```yaml
# .github/workflows/touchstone.yml
name: touchstone
on:
  pull_request:
    paths: ["**/*.sql", "models/**", "migrations/**"]

jobs:
  pr-impact:
    runs-on: ubuntu-latest
    permissions: { pull-requests: write, contents: read }
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv tool install touchstone-cli
      - name: Generate report
        env: { GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }} }
        run: touchstone pr --repo ${{ github.repository }} --pr ${{ github.event.pull_request.number }} --dialect snowflake > report.md
      - name: Comment on PR
        uses: peter-evans/create-or-update-comment@v4
        with:
          issue-number: ${{ github.event.pull_request.number }}
          body-path: report.md
```

This avoids the App entirely but gives you less ergonomic check-run output.

## What the bot does NOT do (by design)

- It does NOT run actual queries against prod. The PR report comes from
  parsing the SQL diff, not executing it.
- It does NOT modify your code. Suggested tests are emitted as a block in
  the PR comment; merging them is up to you.
- It does NOT learn from your accept/reject behavior in v0.1 — that's a
  roadmap item (the Triage UI will close that loop).

## Troubleshooting

- **No comment posted on a PR that should have one**: check the App's
  webhook deliveries page in GitHub for failed deliveries.
- **`touchstone` CLI not found**: set `TOUCHSTONE_CLI` to its absolute path,
  or use the combined `touchstone-github` image which bundles both.
- **Check run shows "neutral" but you wanted "failure"**: tune
  `.touchstone.yml`'s `checks.fail_on_*` knobs.
