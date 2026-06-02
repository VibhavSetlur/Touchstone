"""ClickHouse connector."""

from __future__ import annotations

from touchstone.connectors._sqlalchemy_base import SQLAlchemyConnector, resolve_password
from touchstone.types import ConnectorError, Engine


class ClickHouseConnector(SQLAlchemyConnector):
    engine = Engine.CLICKHOUSE

    def dialect_url(self) -> str:
        try:
            import clickhouse_sqlalchemy  # noqa: F401
        except ImportError as e:
            raise ConnectorError(
                "clickhouse-sqlalchemy not installed. "
                "Install with: pip install 'touchstone-core[clickhouse]'"
            ) from e
        password = resolve_password(self.config.password_ref)
        creds = f"{self.config.user}:{password}@" if self.config.user else ""
        return (
            f"clickhouse+http://{creds}{self.config.host}:{self.config.port or 8123}"
            f"/{self.config.database or 'default'}"
        )
