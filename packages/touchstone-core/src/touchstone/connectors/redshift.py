"""Redshift connector (via redshift-connector + SQLAlchemy)."""

from __future__ import annotations

from touchstone.connectors._sqlalchemy_base import SQLAlchemyConnector, resolve_password
from touchstone.types import ConnectorError, Engine


class RedshiftConnector(SQLAlchemyConnector):
    engine = Engine.REDSHIFT

    def dialect_url(self) -> str:
        try:
            import sqlalchemy_redshift  # noqa: F401
        except ImportError as e:
            raise ConnectorError(
                "sqlalchemy-redshift not installed. "
                "Install with: pip install 'touchstone-core[redshift]'"
            ) from e
        password = resolve_password(self.config.password_ref)
        return (
            f"redshift+redshift_connector://{self.config.user}:{password}"
            f"@{self.config.host}:{self.config.port or 5439}/{self.config.database}"
        )
