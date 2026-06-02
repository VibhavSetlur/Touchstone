"""PostgreSQL connector via psycopg 3.

Uses native parameter binding (no string interpolation), server-side
statement_timeout, and an explicit read-only transaction when configured.
"""

from __future__ import annotations

import sys
from typing import Any

from touchstone.connectors.base import Connector
from touchstone.secrets import resolve
from touchstone.types import (
    Column,
    ConnectorAuthError,
    ConnectorError,
    ConnectorTimeoutError,
    Engine,
    QueryResult,
    TableRef,
)


class PostgresConnector(Connector):
    engine = Engine.POSTGRES

    def connect(self) -> None:
        if self._connection is not None:
            return
        try:
            import psycopg
        except ImportError as e:
            raise ConnectorError(
                "psycopg is not installed. Install with: pip install 'touchstone-core[postgres]'"
            ) from e

        password = resolve(self.config.password_ref) if self.config.password_ref else None
        try:
            self._connection = psycopg.connect(
                host=self.config.host,
                port=self.config.port or 5432,
                dbname=self.config.database,
                user=self.config.user,
                password=password,
                connect_timeout=10,
                application_name="touchstone",
                autocommit=False,
            )
            self._set_session_guards()
        except psycopg.OperationalError as e:
            msg = _scrub(str(e))
            if "authentication" in msg.lower() or "password" in msg.lower():
                raise ConnectorAuthError(msg) from None
            raise ConnectorError(msg) from None
        finally:
            del password  # don't keep it around

    def _set_session_guards(self) -> None:
        with self._connection.cursor() as cur:
            cur.execute(f"SET statement_timeout = {self.config.timeout_seconds * 1000}")
            cur.execute("SET lock_timeout = 5000")
            cur.execute("SET idle_in_transaction_session_timeout = 30000")
            if self.config.read_only:
                cur.execute("SET default_transaction_read_only = on")
        self._connection.commit()

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        if self._connection is None:
            self.connect()
        import psycopg

        try:
            with self._connection.cursor() as cur:
                if self.config.read_only:
                    cur.execute("BEGIN READ ONLY")
                cur.execute(sql, params or {})

                if cur.description is None:
                    # DDL/DML that returned no rows
                    self._connection.commit() if not self.config.read_only else self._connection.rollback()
                    return QueryResult(
                        columns=[],
                        rows=[],
                        row_count=cur.rowcount if cur.rowcount >= 0 else 0,
                        engine=Engine.POSTGRES,
                        sql_executed=sql,
                    )

                columns = [
                    Column(name=desc.name, type=_pg_type_name(desc.type_code))
                    for desc in cur.description
                ]

                rows: list[tuple[Any, ...]] = []
                bytes_returned = 0
                for row in cur:
                    rows.append(tuple(row))
                    bytes_returned += sys.getsizeof(row)
                    if (
                        len(rows) >= self.config.row_cap
                        or bytes_returned >= self.config.byte_cap
                    ):
                        break
                truncated = (
                    len(rows) >= self.config.row_cap or bytes_returned >= self.config.byte_cap
                )
                self._connection.rollback()  # never commit in read-only mode
                return QueryResult(
                    columns=columns,
                    rows=rows,
                    row_count=len(rows),
                    truncated=truncated,
                    bytes_returned=bytes_returned,
                    engine=Engine.POSTGRES,
                    sql_executed=sql,
                )

        except psycopg.errors.QueryCanceled as e:
            self._connection.rollback()
            raise ConnectorTimeoutError(_scrub(str(e))) from None
        except psycopg.Error as e:
            self._connection.rollback()
            raise ConnectorError(_scrub(str(e))) from None

    def list_tables(self, schema: str | None = None) -> list[TableRef]:
        if self._connection is None:
            self.connect()
        sql = """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        """
        params: dict[str, Any] = {}
        if schema:
            sql += " AND table_schema = %(schema)s"
            params["schema"] = schema
        result = self.execute(sql, params)
        return [TableRef(name=str(r[1]), schema=str(r[0])) for r in result.rows]

    def describe_table(self, table: TableRef) -> list[Column]:
        sql = """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %(schema)s AND table_name = %(table)s
            ORDER BY ordinal_position
        """
        result = self.execute(
            sql, {"schema": table.schema or "public", "table": table.name}
        )
        return [
            Column(name=str(r[0]), type=str(r[1]), nullable=(str(r[2]) == "YES"))
            for r in result.rows
        ]


# Postgres OIDs we care about — full mapping in pg_type, but a handful covers
# 99% of normal app schemas. Anything else falls through as "unknown".
_PG_OID_NAMES = {
    16: "boolean", 20: "bigint", 21: "smallint", 23: "integer",
    25: "text", 700: "real", 701: "double precision",
    1042: "char", 1043: "varchar",
    1082: "date", 1083: "time", 1114: "timestamp", 1184: "timestamptz",
    1700: "numeric", 2950: "uuid", 3802: "jsonb", 114: "json",
}


def _pg_type_name(oid: int) -> str:
    return _PG_OID_NAMES.get(oid, f"oid:{oid}")


def _scrub(msg: str) -> str:
    import re
    msg = re.sub(r"password=[^ ;,)]+", "password=***", msg, flags=re.IGNORECASE)
    msg = re.sub(r"//[^:]+:[^@]+@", "//***:***@", msg)
    return msg
