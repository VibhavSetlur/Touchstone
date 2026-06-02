"""DuckDB connector.

DuckDB is our primary testing engine — fast, in-process, file-or-memory, supports
99% of SQL anyone would write. The connector also doubles as the default for
local QA against parquet/CSV files.
"""

from __future__ import annotations

import sys
from typing import Any

from touchstone.connectors.base import Connector
from touchstone.types import (
    Column,
    ConnectorError,
    Engine,
    QueryResult,
    TableRef,
)


class DuckDBConnector(Connector):
    engine = Engine.DUCKDB

    def connect(self) -> None:
        if self._connection is not None:
            return
        try:
            import duckdb
        except ImportError as e:
            raise ConnectorError(
                "duckdb is not installed. Install with: pip install duckdb"
            ) from e
        path = self.config.database or ":memory:"
        read_only = bool(self.config.read_only and path != ":memory:")
        self._connection = duckdb.connect(path, read_only=read_only)

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
            truncated = len(rows) >= self.config.row_cap or bytes_returned >= self.config.byte_cap
            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=truncated,
                bytes_returned=bytes_returned,
                engine=Engine.DUCKDB,
                sql_executed=sql,
            )
        except Exception as e:
            raise ConnectorError(_scrub(str(e))) from None

    def list_tables(self, schema: str | None = None) -> list[TableRef]:
        if self._connection is None:
            self.connect()
        where = f"WHERE table_schema = '{schema}'" if schema else ""
        result = self._connection.execute(
            f"SELECT table_schema, table_name FROM information_schema.tables {where}"
        ).fetchall()
        return [TableRef(name=t, schema=s) for s, t in result]

    def describe_table(self, table: TableRef) -> list[Column]:
        if self._connection is None:
            self.connect()
        qualified = f"{table.schema}.{table.name}" if table.schema else table.name
        result = self._connection.execute(f"DESCRIBE {qualified}").fetchall()
        cols: list[Column] = []
        for row in result:
            # DuckDB DESCRIBE returns: column_name, column_type, null, key, default, extra
            cols.append(Column(
                name=row[0],
                type=row[1],
                nullable=(row[2] == "YES"),
                primary_key=(row[3] == "PRI"),
            ))
        return cols


def _scrub(msg: str) -> str:
    """Remove anything that might be a credential before raising. Defensive
    only — DuckDB rarely embeds creds in errors, but we want one canonical
    scrubbing path."""
    import re
    msg = re.sub(r"password=[^ ;,)]+", "password=***", msg, flags=re.IGNORECASE)
    msg = re.sub(r"pwd=[^ ;,)]+", "pwd=***", msg, flags=re.IGNORECASE)
    return msg
