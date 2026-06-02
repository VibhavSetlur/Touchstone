# Snowflake connector

## Driver

`snowflake-connector-python`. Install with:

```bash
pip install 'touchstone-core[snowflake]'
```

## Auth methods

Touchstone supports four:

| Method            | When to use                                                    |
| ----------------- | -------------------------------------------------------------- |
| `password`        | Quick tests. Avoid in prod.                                    |
| `key_pair`        | Recommended for production.                                    |
| `externalbrowser` | Local dev with SSO.                                            |
| `oauth`           | Inside an OAuth2 gateway that brokers Snowflake tokens.        |

### Key-pair (recommended)

```bash
openssl genrsa -out touchstone_rsa.pem 2048
openssl pkcs8 -topk8 -inform PEM -outform PEM -nocrypt \
  -in touchstone_rsa.pem -out touchstone_rsa.p8
openssl rsa -in touchstone_rsa.pem -pubout -out touchstone_rsa.pub
```

In Snowflake:

```sql
ALTER USER touchstone_svc SET RSA_PUBLIC_KEY='<contents of .pub minus header/footer>';
```

In config:

```toml
[connections.prod-ro]
engine = "snowflake"
user = "touchstone_svc"
database = "PROD"
schema = "PUBLIC"

[connections.prod-ro.extra]
account = "acme"
warehouse = "TOUCHSTONE_XS"
role = "TOUCHSTONE_RO"
auth = "key_pair"
private_key_file = "/etc/touchstone/touchstone_rsa.p8"
```

## Session parameters

The connector sets:

- `STATEMENT_TIMEOUT_IN_SECONDS = <timeout_seconds>` — hard timeout
- `QUERY_TAG = 'touchstone'` — so Snowflake's `QUERY_HISTORY` view can
  identify Touchstone queries

## Cost guardrails

By default, the connector doesn't impose a credit limit. For cost-conscious
deployments:

- Use a dedicated `XSMALL` warehouse with `AUTO_SUSPEND = 60`.
- Apply a [resource monitor](https://docs.snowflake.com/en/user-guide/resource-monitors)
  to that warehouse.
- Tag the connection `cost-controlled` and use a policy rule that requires
  consent for any `MERGE` / `CREATE TABLE AS SELECT` against it.

A per-query bytes-scanned cap is on the roadmap.

## Permissions for the touchstone role

```sql
CREATE ROLE TOUCHSTONE_RO;
GRANT USAGE ON WAREHOUSE TOUCHSTONE_XS TO ROLE TOUCHSTONE_RO;
GRANT USAGE ON DATABASE PROD TO ROLE TOUCHSTONE_RO;
GRANT USAGE ON SCHEMA PROD.PUBLIC TO ROLE TOUCHSTONE_RO;
GRANT SELECT ON ALL TABLES IN SCHEMA PROD.PUBLIC TO ROLE TOUCHSTONE_RO;
GRANT SELECT ON FUTURE TABLES IN SCHEMA PROD.PUBLIC TO ROLE TOUCHSTONE_RO;

CREATE USER touchstone_svc
  TYPE = SERVICE
  DEFAULT_ROLE = TOUCHSTONE_RO
  DEFAULT_WAREHOUSE = TOUCHSTONE_XS;
GRANT ROLE TOUCHSTONE_RO TO USER touchstone_svc;
```

If you use [dynamic data masking](https://docs.snowflake.com/en/user-guide/security-column-ddm)
on PII columns, Touchstone's PII detection complements it — Snowflake masks
at the storage layer, Touchstone catches anything that slipped through (for
example, a free-text column with an email pasted into it).
