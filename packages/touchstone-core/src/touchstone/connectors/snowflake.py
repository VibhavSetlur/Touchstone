"""Snowflake connector.

Supports password, key-pair (recommended), and externalbrowser (SSO) auth.
Honors STATEMENT_TIMEOUT_IN_SECONDS server-side. Tags every session with
APP=touchstone so audit teams can correlate queries on the Snowflake side.
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


class SnowflakeConnector(Connector):
    engine = Engine.SNOWFLAKE

    def connect(self) -> None:
        if self._connection is not None:
            return
        try:
            import snowflake.connector
        except ImportError as e:
            raise ConnectorError(
                "snowflake-connector-python not installed. "
                "Install with: pip install 'touchstone-core[snowflake]'"
            ) from e

        auth = self.config.extra.get("auth", "password")
        kwargs: dict[str, Any] = {
            "account": self.config.extra["account"],
            "user": self.config.user,
            "database": self.config.database,
            "schema": self.config.schema_,
            "warehouse": self.config.extra.get("warehouse"),
            "role": self.config.extra.get("role"),
            "session_parameters": {
                "STATEMENT_TIMEOUT_IN_SECONDS": self.config.timeout_seconds,
                "QUERY_TAG": "touchstone",
            },
            "client_session_keep_alive": False,
            "application": "touchstone",
        }

        if auth == "password":
            kwargs["password"] = resolve(self.config.password_ref) if self.config.password_ref else None
        elif auth == "key_pair":
            kwargs["private_key_file"] = self.config.extra["private_key_file"]
            kwargs["private_key_file_pwd"] = (
                resolve(self.config.password_ref) if self.config.password_ref else None
            )
        elif auth == "externalbrowser":
            kwargs["authenticator"] = "externalbrowser"
        elif auth == "oauth":
            kwargs["authenticator"] = "oauth"
            kwargs["token"] = resolve(self.config.password_ref) if self.config.password_ref else None
        else:
            raise ConnectorError(f"unknown Snowflake auth method: {auth!r}")

        try:
            self._connection = snowflake.connector.connect(**kwargs)
        except snowflake.connector.errors.DatabaseError as e:
            msg = _scrub(str(e))
            if "Incorrect username or password" in msg or "Authentication" in msg:
                raise ConnectorAuthError(msg) from None
            raise ConnectorError(msg) from None

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        if self._connection is None:
            self.connect()
        import snowflake.connector

        try:
            cur = self._connection.cursor()
            try:
                # Snowflake uses qmark/numeric/named depending on session;
                # use named (%(key)s style) consistently.
                cur.execute(sql, params or {})
                if cur.description is None:
                    return QueryResult(
                        columns=[], rows=[], row_count=cur.rowcount or 0,
                        engine=Engine.SNOWFLAKE, sql_executed=sql,
                    )
                columns = [
                    Column(name=desc.name, type=desc.type_code)
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
                return QueryResult(
                    columns=columns, rows=rows, row_count=len(rows),
                    truncated=(len(rows) >= self.config.row_cap),
                    bytes_returned=bytes_returned, engine=Engine.SNOWFLAKE,
                    sql_executed=sql,
                )
            finally:
                cur.close()
        except snowflake.connector.errors.ProgrammingError as e:
            msg = _scrub(str(e))
            if "Statement reached its statement or warehouse timeout" in msg:
                raise ConnectorTimeoutError(msg) from None
            raise ConnectorError(msg) from None

    def list_tables(self, schema: str | None = None) -> list[TableRef]:
        schema = schema or self.config.schema_ or "PUBLIC"
        sql = """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema = %(schema)s
        """
        result = self.execute(sql, {"schema": schema})
        return [TableRef(name=str(r[1]), schema=str(r[0])) for r in result.rows]

    def describe_table(self, table: TableRef) -> list[Column]:
        sql = """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %(schema)s AND table_name = %(table)s
            ORDER BY ordinal_position
        """
        result = self.execute(
            sql, {"schema": table.schema or "PUBLIC", "table": table.name}
        )
        return [
            Column(name=str(r[0]), type=str(r[1]), nullable=(str(r[2]) == "YES"))
            for r in result.rows
        ]


def _scrub(msg: str) -> str:
    import re
    msg = re.sub(r"password=[^ ;,)]+", "password=***", msg, flags=re.IGNORECASE)
    msg = re.sub(r"token=[^ ;,)]+", "token=***", msg, flags=re.IGNORECASE)
    return msg
