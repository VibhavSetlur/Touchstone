# Snowflake enterprise deployment

A reference deployment for a mid-size data team:

- Snowflake (prod + dev) accessed via key-pair auth.
- Touchstone MCP runs as a remote server inside the VPC behind an OAuth2
  proxy (Authelia / Authentik / Okta).
- Audit logs ship to S3 with object-lock enabled.
- Consent prompts go to a private Slack channel.
- Developers' AI assistants (Claude / Copilot / Cursor) connect over
  Streamable HTTP.

## 1. Generate the Snowflake key pair

```bash
openssl genrsa -out touchstone_rsa.pem 2048
openssl rsa -in touchstone_rsa.pem -pubout -out touchstone_rsa.pub
```

In Snowflake, attach the public key to a service-account role with the
narrowest privileges that satisfy your QA needs:

```sql
CREATE ROLE TOUCHSTONE_RO;
GRANT USAGE ON DATABASE prod TO ROLE TOUCHSTONE_RO;
GRANT USAGE ON SCHEMA prod.public TO ROLE TOUCHSTONE_RO;
GRANT SELECT ON ALL TABLES IN SCHEMA prod.public TO ROLE TOUCHSTONE_RO;
GRANT SELECT ON FUTURE TABLES IN SCHEMA prod.public TO ROLE TOUCHSTONE_RO;

CREATE USER touchstone_svc
  TYPE = SERVICE
  DEFAULT_ROLE = TOUCHSTONE_RO
  DEFAULT_WAREHOUSE = TOUCHSTONE_XS
  RSA_PUBLIC_KEY = '...';
```

## 2. touchstone.toml

See `touchstone.toml` in this directory.

## 3. Deploy

```bash
docker build -t touchstone-mcp:0.1 -f docker/touchstone-mcp.Dockerfile .
helm install touchstone deploy/helm \
  --set image.repository=touchstone-mcp \
  --set image.tag=0.1 \
  --set audit.s3.bucket=acme-touchstone-audit \
  --set consent.slack.webhook=https://hooks.slack.com/services/...
```

(Helm chart shipped as a stub — full chart on the roadmap.)

## 4. Connect AI assistants

For Claude Code / Cursor / Continue with remote MCP:

```json
{
  "mcpServers": {
    "touchstone": {
      "url": "https://touchstone.internal/mcp",
      "auth": {"type": "oauth2", "issuer": "https://sso.acme.com"}
    }
  }
}
```

## What you get

- Every dev's AI assistant can read prod (PII-masked) without a Snowflake
  login.
- Every query is in S3 with a 7-year retention, hash-chained.
- Sensitive ops (anything tagged `prod`, anything non-SELECT) require an
  approver in Slack — provable trail of who approved what.
- Snowflake-side audit (QUERY_HISTORY) also sees these queries because
  Touchstone tags them with `QUERY_TAG = 'touchstone'`.
