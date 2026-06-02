"""Shared SQLAlchemy-based connector skeleton.

Databricks, Redshift, Trino, ClickHouse all have native drivers that follow
DB-API 2.0 closely enough that a single thin base class covers them. Each
connector is then ~50 lines: a connect URL builder, optional vendor session
guards, and the engine enum.
"""

from __future__ import annotations

import sys
from typing import Any

from touchstone.connectors.base import Connector
from touchstone.secrets import resolve
from touchstone.types import (
    Column,
    ConnectorError,
    ConnectorTimeoutError,
    Engine,
    QueryResult,
    TableRef,
)


class SQLAlchemyConnector(Connector):
    """Base for SQLAlchemy-backed connectors. Subclasses set:

      - engine (Engine enum value)
      - dialect_url(): SQLAlchemy URL string
      - session_guards(conn): vendor-specific session SETs
      - list_tables_sql / describe_table_sql: portable enough to override only
        when needed.
    """

    def connect(self) -> None:
        if self._connection is not None:
            return
        try:
            from sqlalchemy import create_engine
        except ImportError as e:
            raise ConnectorError("sqlalchemy not installed.") from e
        url = self.dialect_url()
        sa_engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args=self.connect_args(),
        )
        self._connection = sa_engine.connect()
        self.session_guards(self._connection)

    def dialect_url(self) -> str:
        raise NotImplementedError

    def connect_args(self) -> dict[str, Any]:
        return {}

    def session_guards(self, conn: Any) -> None:
        return

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        if self._connection is None:
            self.connect()
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError, OperationalError

        try:
            result = self._connection.execute(text(sql), params or {})
            if result.returns_rows is False or result.cursor is None:
                return QueryResult(
                    columns=[], rows=[], row_count=result.rowcount or 0,
                    engine=self.engine, sql_executed=sql,
                )
            columns = [
                Column(name=str(k), type=str(result.cursor.description[i][1]))
                for i, k in enumerate(result.keys())
            ]
            rows: list[tuple[Any, ...]] = []
            bytes_returned = 0
            for row in result:
                t = tuple(row)
                rows.append(t)
                bytes_returned += sys.getsizeof(t)
                if len(rows) >= self.config.row_cap or bytes_returned >= self.config.byte_cap:
                    break
            return QueryResult(
                columns=columns, rows=rows, row_count=len(rows),
                truncated=(len(rows) >= self.config.row_cap),
                bytes_returned=bytes_returned, engine=self.engine, sql_executed=sql,
            )
        except OperationalError as e:
            if "timeout" in str(e).lower():
                raise ConnectorTimeoutError(_scrub(str(e))) from None
            raise ConnectorError(_scrub(str(e))) from None
        except DBAPIError as e:
            raise ConnectorError(_scrub(str(e))) from None

    def list_tables(self, schema: str | None = None) -> list[TableRef]:
        result = self.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            f"{'WHERE table_schema = :schema' if schema else ''}",
            {"schema": schema} if schema else {},
        )
        return [TableRef(name=str(r[1]), schema=str(r[0])) for r in result.rows]

    def describe_table(self, table: TableRef) -> list[Column]:
        result = self.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table ORDER BY ordinal_position",
            {"schema": table.schema, "table": table.name},
        )
        return [
            Column(name=str(r[0]), type=str(r[1]), nullable=(str(r[2]) == "YES"))
            for r in result.rows
        ]


def _scrub(msg: str) -> str:
    import re
    msg = re.sub(r"password=[^ ;,)]+", "password=***", msg, flags=re.IGNORECASE)
    msg = re.sub(r"//[^:]+:[^@]+@", "//***:***@", msg)
    return msg


def resolve_password(ref: str | None) -> str:
    return resolve(ref) if ref else ""
