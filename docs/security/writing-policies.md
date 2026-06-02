# Writing policies

Touchstone policies are YAML files of rules. Each rule has:

- `name` — unique identifier, shows up in the audit log.
- `effect` — `allow` | `deny` | `consent`.
- `assistants` — list of assistant ID globs (`*`, `claude-*`). Default: `["*"]`.
- `connections` — list of connection-name globs. Default: `["*"]`.
- `tools` — list of MCP tool names. Default: `["*"]`.
- `when` — optional expression. Empty = always.
- `priority` — lower runs first. Default: 100. First match wins.

## Default behavior (built-in)

These rules ship with Touchstone and apply at priority 10–200:

| Priority | Effect    | When                                                          |
| -------- | --------- | ------------------------------------------------------------- |
| 10       | deny      | SQL touches `audit_log` or `users.password_hash`              |
| 20       | consent   | Non-SELECT statement                                          |
| 30       | consent   | Connection tagged `prod` (for query/profile/diff tools)       |
| 100      | allow     | SELECT against a read-only connection (broad set of tools)    |
| 200      | allow     | Metadata-only tools (list_tables, describe_table, audit_query) |

Add your own rules at lower priorities to override or extend.

## The condition language

The `when` field is a tiny safe expression DSL. Available names:

- `assistant_id` — string
- `connection` — string
- `tags` — set of strings (the connection's `tags` config)
- `read_only` — boolean (the connection's `read_only` config)
- `tool` — string (the MCP tool name)
- `sql` — dict (the parsed SQL summary)
- `metadata` — dict (operator-provided extra context)

Available operators: `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`, `and`,
`or`, `not`, and attribute/item access.

NOT available: function calls, imports, `eval`, list/dict comprehensions,
anything not on the above list. This is intentional — policy files are
operator-controlled but should not be a code-execution surface.

### `sql` dict shape

- `kind` — `"SELECT"`, `"INSERT"`, `"UPDATE"`, `"DELETE"`, `"CREATE"`,
  `"DROP"`, `"ALTER"`, `"GRANT"`, `"EXPLAIN"`, `"UNPARSEABLE"`, etc.
- `is_select` — `True` for `SELECT`/`WITH`/`UNION`/`EXCEPT`/`INTERSECT`.
- `touches` — list of fully-qualified table names referenced.
- `has_limit` — `True` if the query has a LIMIT clause.
- `joins` — int, count of JOIN clauses.

For Mongo: `sql` is `{"mongo_spec": <raw>, "kind": "mongo", "is_select": True, "touches": []}`.

## Examples

### Deny touching customer PII for non-admin assistants

```yaml
rules:
  - name: deny-customer-pii-for-non-admin
    effect: deny
    assistants: ["*"]
    when: '"customers.email" in sql.touches and "admin" not in tags'
    priority: 5
```

### Require consent on large result sets

```yaml
  - name: consent-large-results
    effect: consent
    when: "not sql.has_limit"
    priority: 40
```

(Heuristic — a query without LIMIT *might* be large. Refine if your
environment has different conventions.)

### Allow Copilot to query dev but not prod

```yaml
  - name: copilot-dev-only
    effect: allow
    assistants: ["copilot"]
    connections: ["*-dev*"]
    priority: 60

  - name: copilot-no-prod
    effect: deny
    assistants: ["copilot"]
    connections: ["*-prod*", "*-prd*"]
    priority: 5
```

### Require consent on cross-PII-boundary joins

If your connection tags include `pii-strict` for PII-bearing tables:

```yaml
  - name: consent-pii-join
    effect: consent
    when: 'sql.joins > 0 and "pii-strict" in tags'
    priority: 25
```

## Versioning policies

Check policy files into the repo where you deploy Touchstone. Treat them
like code — PR review, CI checks, blame history. The audit log records
which rule name matched each decision, so you can answer "why did Touchstone
allow / deny / prompt for X?" against historical events even after the
policy bundle has changed.

## OPA bridge (planned)

For enterprises that already run OPA: a planned `opa_bridge.py` will export
the same decision context to a local OPA daemon and accept its verdict.
Status: roadmap.
