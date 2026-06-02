"""File-based "connection" — turns CSV / Excel / Parquet / JSON / NDJSON
files into something the rest of Touchstone can query.

Implementation: wrap a DuckDB in-memory connection and register each file as
a view. Excel uses DuckDB's excel extension; everything else uses native
readers. This means all of the SQL surface (profile, diff, validate,
lineage) just works against files with zero new code paths.

Config:

    [connections.local-files]
    engine = "files"
    database = "/data/csv-dump"           # base path (file or directory)
    tags = ["dev"]

    [connections.local-files.extra]
    # Optional explicit mapping; otherwise files are auto-registered by their
    # stem (orders.csv -> view `orders`).
    views = {
      orders = "/data/csv-dump/orders.csv",
      customers = "/data/customers.xlsx#Sheet1",
    }
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from touchstone.connectors.base import Connector
from touchstone.types import (
    Column,
    ConnectorError,
    Engine,
    QueryResult,
    TableRef,
)


SUFFIX_TO_READER: dict[str, str] = {
    ".csv": "read_csv_auto",
    ".tsv": "read_csv",
    ".parquet": "read_parquet",
    ".json": "read_json_auto",
    ".ndjson": "read_json_auto",
    ".jsonl": "read_json_auto",
}


class FilesConnector(Connector):
    """Read flat-file data sources through a SQL surface.

    File listing is rebuilt on connect, so adding a new CSV to the watched
    directory and reconnecting picks it up — no schema migration needed.
    """

    engine = Engine.DUCKDB  # piggyback on DuckDB type taxonomy

    def connect(self) -> None:
        if self._connection is not None:
            return
        try:
            import duckdb
        except ImportError as e:
            raise ConnectorError(
                "duckdb not installed. Install with: pip install duckdb"
            ) from e

        self._connection = duckdb.connect(":memory:")
        self._register_views()

    def _register_views(self) -> None:
        base = Path(self.config.database or ".").expanduser()
        explicit = self.config.extra.get("views", {})

        if explicit:
            for view_name, spec in explicit.items():
                self._register_one(view_name, spec)
        elif base.is_file():
            self._register_one(base.stem, str(base))
        elif base.is_dir():
            for f in sorted(base.iterdir()):
                if f.is_file() and f.suffix.lower() in SUFFIX_TO_READER:
                    self._register_one(f.stem, str(f))
                elif f.suffix.lower() in (".xlsx", ".xls"):
                    self._register_one(f.stem, str(f))
        else:
            raise ConnectorError(
                f"files connector: path not found: {base!s}"
            )

    def _register_one(self, view_name: str, spec: str) -> None:
        """Spec may be `path` or `path#sheet`."""
        if not _safe_ident(view_name):
            raise ConnectorError(f"unsafe view name: {view_name!r}")

        if "#" in spec:
            path_str, sheet = spec.split("#", 1)
        else:
            path_str, sheet = spec, None

        path = Path(path_str)
        suffix = path.suffix.lower()

        if suffix in (".xlsx", ".xls"):
            self._connection.execute("INSTALL excel; LOAD excel;")
            sheet_arg = f", sheet='{sheet}'" if sheet else ""
            self._connection.execute(
                f"CREATE OR REPLACE VIEW \"{view_name}\" AS "
                f"SELECT * FROM read_xlsx('{path_str}'{sheet_arg})"
            )
        elif suffix in SUFFIX_TO_READER:
            reader = SUFFIX_TO_READER[suffix]
            self._connection.execute(
                f"CREATE OR REPLACE VIEW \"{view_name}\" AS "
                f"SELECT * FROM {reader}('{path_str}')"
            )
        else:
            raise ConnectorError(f"unsupported file type: {suffix} ({path_str})")

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        if self._connection is None:
            self.connect()
        try:
            cur = self._connection.execute(sql, params or {})
            description = cur.description or []
            columns = [Column(name=col[0], type=str(col[1])) for col in description]
            rows: list[tuple[Any, ...]] = []
            bytes_returned = 0
            for row in cur.fetchall():
                rows.append(tuple(row))
                bytes_returned += sys.getsizeof(row)
                if len(rows) >= self.config.row_cap or bytes_returned >= self.config.byte_cap:
                    break
            return QueryResult(
                columns=columns, rows=rows, row_count=len(rows),
                truncated=(len(rows) >= self.config.row_cap),
                bytes_returned=bytes_returned, engine=Engine.DUCKDB,
                sql_executed=sql,
            )
        except Exception as e:
            raise ConnectorError(str(e)) from None

    def list_tables(self, schema: str | None = None) -> list[TableRef]:
        if self._connection is None:
            self.connect()
        rows = self._connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
        return [TableRef(name=str(r[0])) for r in rows]

    def describe_table(self, table: TableRef) -> list[Column]:
        if self._connection is None:
            self.connect()
        rows = self._connection.execute(f'DESCRIBE "{table.name}"').fetchall()
        return [
            Column(name=str(r[0]), type=str(r[1]),
                   nullable=(str(r[2]) == "YES"))
            for r in rows
        ]


def _safe_ident(name: str) -> bool:
    return bool(name) and all(c.isalnum() or c in "_-" for c in name)
