"""MySQL / MariaDB connector via PyMySQL.

Pure-Python driver — avoids the libmysqlclient build pain across platforms.
Slower than mysqlclient under bulk load but unbeatable for installability.
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


class MySQLConnector(Connector):
    engine = Engine.MYSQL

    def connect(self) -> None:
        if self._connection is not None:
            return
        try:
            import pymysql
        except ImportError as e:
            raise ConnectorError(
                "pymysql is not installed. Install with: pip install 'touchstone-core[mysql]'"
            ) from e

        password = resolve(self.config.password_ref) if self.config.password_ref else None
        try:
            self._connection = pymysql.connect(
                host=self.config.host,
                port=self.config.port or 3306,
                database=self.config.database,
                user=self.config.user,
                password=password or "",
                connect_timeout=10,
                read_timeout=self.config.timeout_seconds,
                write_timeout=self.config.timeout_seconds,
                program_name="touchstone",
                autocommit=False,
            )
            self._set_session_guards()
        except pymysql.err.OperationalError as e:
            msg = _scrub(str(e))
            if "access denied" in msg.lower():
                raise ConnectorAuthError(msg) from None
            raise ConnectorError(msg) from None
        finally:
            del password

    def _set_session_guards(self) -> None:
        with self._connection.cursor() as cur:
            # MAX_EXECUTION_TIME is in ms, applies to SELECT only — that's mostly
            # what we run anyway.
            cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {self.config.timeout_seconds * 1000}")
            cur.execute("SET SESSION lock_wait_timeout = 5")
            if self.config.read_only:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
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
        import pymysql

        try:
            with self._connection.cursor() as cur:
                cur.execute(sql, params or {})

                if cur.description is None:
                    return QueryResult(
                        columns=[], rows=[],
                        row_count=cur.rowcount if cur.rowcount >= 0 else 0,
                        engine=Engine.MYSQL, sql_executed=sql,
                    )

                columns = [
                    Column(name=desc[0], type=_mysql_type_name(desc[1]))
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
                self._connection.rollback()
                return QueryResult(
                    columns=columns, rows=rows, row_count=len(rows),
                    truncated=truncated, bytes_returned=bytes_returned,
                    engine=Engine.MYSQL, sql_executed=sql,
                )

        except pymysql.err.OperationalError as e:
            self._connection.rollback()
            # 3024 = ER_QUERY_TIMEOUT
            if e.args and e.args[0] == 3024:
                raise ConnectorTimeoutError(_scrub(str(e))) from None
            raise ConnectorError(_scrub(str(e))) from None
        except pymysql.Error as e:
            self._connection.rollback()
            raise ConnectorError(_scrub(str(e))) from None

    def list_tables(self, schema: str | None = None) -> list[TableRef]:
        sql = """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys')
        """
        params: dict[str, Any] = {}
        if schema:
            sql += " AND table_schema = %(schema)s"
            params["schema"] = schema
        result = self.execute(sql, params)
        return [TableRef(name=str(r[1]), schema=str(r[0])) for r in result.rows]

    def describe_table(self, table: TableRef) -> list[Column]:
        sql = """
            SELECT column_name, data_type, is_nullable, column_key
            FROM information_schema.columns
            WHERE table_schema = %(schema)s AND table_name = %(table)s
            ORDER BY ordinal_position
        """
        result = self.execute(
            sql, {"schema": table.schema or self.config.database, "table": table.name}
        )
        return [
            Column(
                name=str(r[0]), type=str(r[1]),
                nullable=(str(r[2]) == "YES"),
                primary_key=(str(r[3]) == "PRI"),
            )
            for r in result.rows
        ]


# Minimal MySQL field-type → name map. Use the most common ones.
_MYSQL_TYPE_NAMES = {
    1: "tinyint", 2: "smallint", 3: "int", 4: "float", 5: "double",
    7: "timestamp", 8: "bigint", 9: "mediumint", 10: "date", 11: "time",
    12: "datetime", 13: "year", 15: "varchar", 16: "bit",
    245: "json", 246: "decimal",
    252: "blob", 253: "varstring", 254: "string",
}


def _mysql_type_name(code: int) -> str:
    return _MYSQL_TYPE_NAMES.get(code, f"mysql:{code}")


def _scrub(msg: str) -> str:
    import re
    msg = re.sub(r"password=[^ ;,)]+", "password=***", msg, flags=re.IGNORECASE)
    return msg
