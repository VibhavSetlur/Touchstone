# PostgreSQL connector

## Driver

`psycopg[binary]` (psycopg 3). Install with:

```bash
pip install 'touchstone-core[postgres]'
```

## Config

```toml
[connections.prod-pg]
engine = "postgres"
host = "db.acme.com"
port = 5432
database = "prod"
user = "touchstone_ro"
password_ref = "env://PG_PASSWORD"
read_only = true
tags = ["prod"]
timeout_seconds = 30
```

For long-lived deployments, prefer connection pooling at the proxy level
(PgBouncer, pgpool). Touchstone holds one connection per `with` block,
which keeps short-lived CLI calls cheap but isn't optimized for high-QPS
remote MCP serving.

## Session guards

The connector sets, on every connect:

```sql
SET statement_timeout = <timeout_seconds * 1000>;
SET lock_timeout = 5000;
SET idle_in_transaction_session_timeout = 30000;
SET default_transaction_read_only = on;   -- only when read_only = true
```

And wraps every `SELECT` in `BEGIN READ ONLY ... ROLLBACK`. So even if the
SQL allow-list slipped, the engine itself rejects writes.

## What works

- All standard SELECTs, CTEs, window functions, JSON ops.
- `information_schema` introspection.
- `EXPLAIN` and `EXPLAIN ANALYZE`.
- Named parameters via `%(name)s` placeholders.

## What doesn't (and why)

- `COPY ... TO/FROM` — explicitly denied by default policy. Use a connector
  per spec if you need it.
- `LISTEN`/`NOTIFY` — out of scope.
- Server-side cursors for very large results — TODO; today, large queries
  hit `row_cap` and are truncated.

## Permissions for the touchstone DB role

Minimal:

```sql
CREATE ROLE touchstone_ro NOINHERIT LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE prod TO touchstone_ro;
GRANT USAGE ON SCHEMA public TO touchstone_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO touchstone_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO touchstone_ro;
```

If you've enabled row-level security, Touchstone respects it.
