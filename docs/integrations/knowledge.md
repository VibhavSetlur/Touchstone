# Knowledge store

Touchstone keeps a small SQLite database with the kind of context that's
otherwise scattered across Slack threads, wikis, and tribal memory:

- **Notes** — "the `orders` table has a known timezone quirk on Friday
  batches. See PR #1421."
- **Owners** — who owns which table, model, or path. Sourced from
  CODEOWNERS, augmented manually.
- **Tasks** — open follow-ups the AI tracked from prior conversations.
- **Decisions** — ADR-style "we decided X for reason Y" records.
- **PR digest** — rolling summary of merged PRs indexed by file path, so
  "who changed `customers.email` recently?" works in one query.

## Why this exists

LLMs are stateless across sessions. Every conversation starts fresh. The
knowledge store is where Touchstone remembers between sessions — but
deliberately, with operator review:

- Notes/owners/tasks/decisions are written by the AI through MCP, but the
  audit log captures every write and the human can curate.
- The store is **one SQLite file**. `sqlite3 knowledge.db .dump` produces a
  text snapshot you can check into a private repo.

## Setup

```toml
[knowledge]
path = "~/.touchstone/knowledge.db"
```

Seed it from GitHub:

```bash
touchstone knowledge sync codeowners --repo acme/warehouse
touchstone knowledge sync prs --repo acme/warehouse --limit 200
```

## Day-to-day usage

```bash
# Add a note about a table.
touchstone knowledge note add "table:orders" \
  "The created_at column is UTC in app code but local in the warehouse load. Avoid joining without a tz cast."

# Search.
touchstone knowledge note search "timezone"

# Open and list tasks.
touchstone knowledge tasks
```

From the AI side via MCP:

- `add_note(key, body)` — write a note.
- `search_notes(query)` — full-text search.
- `notes_for(key)` — list notes for a specific key.
- `who_owns(path)` — find owners for a file path.
- `add_task / list_open_tasks / close_task` — track follow-ups.
- `record_decision / decisions_affecting` — capture ADRs.

## Key naming convention

Use prefixed keys so different "things" don't collide:

| Prefix       | Example                          | What it points to                |
| ------------ | -------------------------------- | -------------------------------- |
| `table:`     | `table:orders`                   | A database table.                |
| `column:`    | `column:orders.discount_pct`     | A specific column.               |
| `dashboard:` | `dashboard:looker/42`            | A dashboard URL or ID.           |
| `repo:`      | `repo:acme/warehouse`            | A whole repo.                    |
| `path:`      | `path:models/marts/`             | A code path (matches CODEOWNERS).|
| `metric:`    | `metric:daily_active_users`      | A business metric name.          |

The owner resolver walks `path:` prefixes from deepest to shallowest, so a
note on `path:models/marts/orders.sql` falls back to `path:models/marts/`
to `path:models/` to `path:` (the root) when looking up owners.

## Privacy

The knowledge store holds **metadata only**, not warehouse data. Don't paste
PII into notes — there's no PII detector running on free-form note bodies.
If you need to reference a specific row, reference the key (e.g.
`order_id=42`) rather than its PII (e.g. the customer's email).
