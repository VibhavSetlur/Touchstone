"""SQLite connector (stdlib only).

SQLite is the fallback default — every Python install has it. Useful for
ad-hoc analysis of `.sqlite` files (which surprisingly many enterprise tools
emit: Slack export, browser history, mobile app dumps, etc.).
"""

from __future__ import annotations

import sqlite3
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


class SQLiteConnector(Connector):
    engine = Engine.SQLITE

    def connect(self) -> None:
        if self._connection is not None:
            return
        path = self.config.database or ":memory:"
        flags = "?mode=ro" if self.config.read_only and path != ":memory:" else ""
        uri = f"file:{path}{flags}"
        try:
            self._connection = sqlite3.connect(uri, uri=True, timeout=10)
            self._connection.execute(f"PRAGMA busy_timeout = {self.config.timeout_seconds * 1000}")
        except sqlite3.Error as e:
            raise ConnectorError(str(e)) from None

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        if self._connection is None:
            self.connect()
        try:
            cur = self._connection.execute(sql, params or {})
            if cur.description is None:
                return QueryResult(
                    columns=[], rows=[],
                    row_count=cur.rowcount if cur.rowcount >= 0 else 0,
                    engine=Engine.SQLITE, sql_executed=sql,
                )
            columns = [Column(name=desc[0], type="any") for desc in cur.description]
            rows: list[tuple[Any, ...]] = []
            bytes_returned = 0
            for row in cur:
                rows.append(tuple(row))
                bytes_returned += sys.getsizeof(row)
                if len(rows) >= self.config.row_cap or bytes_returned >= self.config.byte_cap:
                    break
            return QueryResult(
                columns=columns, rows=rows, row_count=len(rows),
                truncated=(len(rows) >= self.config.row_cap),
                bytes_returned=bytes_returned, engine=Engine.SQLITE,
                sql_executed=sql,
            )
        except sqlite3.Error as e:
            raise ConnectorError(str(e)) from None

    def list_tables(self, schema: str | None = None) -> list[TableRef]:
        result = self.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
            "AND name NOT LIKE 'sqlite_%'"
        )
        return [TableRef(name=str(r[0])) for r in result.rows]

    def describe_table(self, table: TableRef) -> list[Column]:
        # PRAGMA table_info doesn't accept parameters, but `table.name` is
        # operator-controlled config, not user input — still, validate.
        if not _safe_ident(table.name):
            raise ConnectorError(f"invalid table name: {table.name!r}")
        result = self.execute(f"PRAGMA table_info({table.name})")
        # PRAGMA returns: cid, name, type, notnull, dflt_value, pk
        return [
            Column(
                name=str(r[1]), type=str(r[2]),
                nullable=(int(r[3]) == 0), primary_key=(int(r[5]) != 0),
            )
            for r in result.rows
        ]


def _safe_ident(name: str) -> bool:
    return all(c.isalnum() or c == "_" for c in name)
