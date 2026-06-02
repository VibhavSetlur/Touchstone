# touchstone-cli

The Touchstone CLI — `touchstone` command for local QA work, audit log
inspection, and standalone use without an AI assistant.

## Install

```bash
pip install -e packages/touchstone-core[quickstart] -e packages/touchstone-cli
```

## Use

```bash
touchstone --version
touchstone init                       # interactive config wizard
touchstone doctor                     # diagnose your setup
touchstone connections                # list configured connections
touchstone profile <conn> <table>     # profile a table
touchstone diff <left> <right> <tbl>  # diff schema/rows across envs
touchstone check <conn> <yaml>        # run data-quality expectations
touchstone pr --repo X --pr N         # PR-impact report from GitHub
touchstone who <repo> <path>          # find owners / recent committers
touchstone knowledge note add ...     # capture context
touchstone playbook list / run        # pre-canned QA workflows
touchstone session bootstrap <cred>   # MFA-aware browser session capture
touchstone audit verify               # check audit log integrity
touchstone serve-mcp                  # run MCP server (also via touchstone-mcp)
```

See https://github.com/VibhavSetlur/Touchstone for full docs.

## License

Apache-2.0.
