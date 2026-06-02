"""Databricks SQL Warehouse connector."""

from __future__ import annotations

from typing import Any

from touchstone.connectors._sqlalchemy_base import SQLAlchemyConnector, resolve_password
from touchstone.types import ConnectorError, Engine


class DatabricksConnector(SQLAlchemyConnector):
    engine = Engine.DATABRICKS

    def dialect_url(self) -> str:
        try:
            import databricks.sqlalchemy  # noqa: F401
        except ImportError as e:
            raise ConnectorError(
                "databricks-sql-connector not installed. "
                "Install with: pip install 'touchstone-core[databricks]'"
            ) from e
        host = self.config.host
        http_path = self.config.extra["http_path"]
        token = resolve_password(self.config.password_ref)
        catalog = self.config.database or "main"
        schema = self.config.schema_ or "default"
        return (
            f"databricks://token:{token}@{host}?http_path={http_path}"
            f"&catalog={catalog}&schema={schema}"
        )

    def session_guards(self, conn: Any) -> None:
        from sqlalchemy import text
        conn.execute(text(f"SET STATEMENT_TIMEOUT = {self.config.timeout_seconds}"))
