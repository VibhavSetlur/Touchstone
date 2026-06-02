# Postgres quickstart

The fastest way to see Touchstone working end-to-end: bring up a Postgres with
some realistic-looking customer data, point Touchstone at it, ask an AI
assistant to QA it.

## 1. Bring up the stack

```bash
cd touchstone
docker compose up -d postgres
```

The `init.sql` here is mounted into the Postgres image and creates a small
`shop` database with `customers`, `orders`, `order_items`, and `payments`.

## 2. Write a Touchstone config

```bash
cp examples/postgres-quickstart/touchstone.toml ~/.touchstone/config.toml
export POSTGRES_PASSWORD=touchstone
```

## 3. Try the CLI

```bash
touchstone connections
touchstone profile local-pg customers
touchstone profile local-pg orders --suggest-tests
touchstone check local-pg examples/postgres-quickstart/expectations.yaml
```

## 4. Point Claude Code at it

Add to `~/.claude/mcp_servers.json`:

```json
{
  "mcpServers": {
    "touchstone": {
      "command": "touchstone-mcp",
      "args": ["--config", "~/.touchstone/config.toml"]
    }
  }
}
```

Then ask Claude: *"Profile the `customers` table and tell me which columns
contain PII."*

You should see Claude call `profile_table_tool` — and the result will have
the email/phone/SSN values already masked.
