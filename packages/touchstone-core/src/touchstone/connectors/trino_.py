"""Trino / Presto connector."""

from __future__ import annotations

from touchstone.connectors._sqlalchemy_base import SQLAlchemyConnector, resolve_password
from touchstone.types import ConnectorError, Engine


class TrinoConnector(SQLAlchemyConnector):
    engine = Engine.TRINO

    def dialect_url(self) -> str:
        try:
            import trino.sqlalchemy  # noqa: F401
        except ImportError as e:
            raise ConnectorError(
                "trino not installed. Install with: pip install 'touchstone-core[trino]'"
            ) from e
        creds = ""
        if self.config.user:
            password = resolve_password(self.config.password_ref)
            creds = f"{self.config.user}:{password}@" if password else f"{self.config.user}@"
        return (
            f"trino://{creds}{self.config.host}:{self.config.port or 8080}"
            f"/{self.config.database or 'system'}/{self.config.schema_ or 'information_schema'}"
        )
