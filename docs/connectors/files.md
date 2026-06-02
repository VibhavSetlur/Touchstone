# Files connector — CSV, Excel, Parquet, JSON

Read flat files as if they were a database. Backed by DuckDB, so the entire
SQL surface (profile, diff, validate, lineage) Just Works against them.

## When to use

- An analyst sent you a `customers.xlsx` dump and wants you to QA it.
- A data export from a SaaS app lands as `events_2026_06_02.parquet` daily.
- A repo has a directory of `*.csv` test fixtures and you want to profile
  them.
- You're auditing a legacy SQLite file (browser history, mobile app dump).

## Config

```toml
[connections.local-files]
engine = "files"
database = "/data/csv-dump"  # file or directory
tags = ["dev"]

[connections.local-files.extra]
# Optional: explicit view name → path mapping. If absent, every file in the
# directory is auto-registered by stem (orders.csv -> view `orders`).
views = {
  orders    = "/data/orders.csv",
  customers = "/data/customers.xlsx#Sheet1",
  events    = "/data/events.parquet",
}
```

## Supported formats

| Suffix     | Reader              |
| ---------- | ------------------- |
| `.csv`     | `read_csv_auto`     |
| `.tsv`     | `read_csv`          |
| `.parquet` | `read_parquet`      |
| `.json`    | `read_json_auto`    |
| `.ndjson`, `.jsonl` | `read_json_auto` |
| `.xlsx`, `.xls` | `read_xlsx` (DuckDB excel extension) |

For Excel files, append `#SheetName` to the path to pick a specific sheet:

```toml
views = { sales_q1 = "/data/quarterly.xlsx#Q1 Sales" }
```

## Try it

```bash
touchstone profile local-files customers
touchstone profile local-files customers --suggest-tests
touchstone check local-files my-expectations.yaml
```

## Limits

- **One process per connection**. Files are loaded into a per-connection
  DuckDB. For large files this is fast (parquet is mmap'd) but for many
  files it can take a few seconds at first connect.
- **Schema is what DuckDB infers**. For ambiguous CSVs (mixed types in a
  column, edge dates), DuckDB can guess wrong. Either pre-cast, or write a
  manual `views = { ... }` with `read_csv(..., types={...})` in your own
  view DDL (planned: per-file type overrides in config).
- **No write-back**. The connector is read-only. To modify a CSV, edit it
  outside Touchstone and reconnect.
