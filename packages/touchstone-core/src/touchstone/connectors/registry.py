"""Connector registry — maps Engine enum to Connector class.

Lazy import: importing a connector triggers importing its driver dependencies,
and we don't want a Postgres-only user to need snowflake-connector-python.
"""

from __future__ import annotations

from typing import Callable

from touchstone.config import ConnectionConfig
from touchstone.connectors.base import Connector
from touchstone.types import ConfigError, Engine

_LOADERS: dict[Engine, Callable[[], type[Connector]]] = {}


def register(engine: Engine, loader: Callable[[], type[Connector]]) -> None:
    _LOADERS[engine] = loader


def _load_postgres() -> type[Connector]:
    from touchstone.connectors.postgres import PostgresConnector
    return PostgresConnector


def _load_mysql() -> type[Connector]:
    from touchstone.connectors.mysql import MySQLConnector
    return MySQLConnector


def _load_duckdb() -> type[Connector]:
    from touchstone.connectors.duckdb_ import DuckDBConnector
    return DuckDBConnector


def _load_sqlite() -> type[Connector]:
    from touchstone.connectors.sqlite import SQLiteConnector
    return SQLiteConnector


def _load_snowflake() -> type[Connector]:
    from touchstone.connectors.snowflake import SnowflakeConnector
    return SnowflakeConnector


def _load_bigquery() -> type[Connector]:
    from touchstone.connectors.bigquery import BigQueryConnector
    return BigQueryConnector


def _load_mongodb() -> type[Connector]:
    from touchstone.connectors.mongodb import MongoDBConnector
    return MongoDBConnector


def _load_databricks() -> type[Connector]:
    from touchstone.connectors.databricks import DatabricksConnector
    return DatabricksConnector


def _load_redshift() -> type[Connector]:
    from touchstone.connectors.redshift import RedshiftConnector
    return RedshiftConnector


def _load_trino() -> type[Connector]:
    from touchstone.connectors.trino_ import TrinoConnector
    return TrinoConnector


def _load_clickhouse() -> type[Connector]:
    from touchstone.connectors.clickhouse import ClickHouseConnector
    return ClickHouseConnector


def _load_files() -> type[Connector]:
    from touchstone.connectors.files import FilesConnector
    return FilesConnector


register(Engine.POSTGRES, _load_postgres)
register(Engine.MYSQL, _load_mysql)
register(Engine.DUCKDB, _load_duckdb)
register(Engine.SQLITE, _load_sqlite)
register(Engine.SNOWFLAKE, _load_snowflake)
register(Engine.BIGQUERY, _load_bigquery)
register(Engine.MONGODB, _load_mongodb)
register(Engine.DATABRICKS, _load_databricks)
register(Engine.REDSHIFT, _load_redshift)
register(Engine.TRINO, _load_trino)
register(Engine.CLICKHOUSE, _load_clickhouse)
register(Engine.FILES, _load_files)


REGISTRY: dict[Engine, Callable[[], type[Connector]]] = _LOADERS


def get_connector(config: ConnectionConfig) -> Connector:
    """Instantiate a connector for the given config. Driver imports happen here,
    not at module load, so missing optional deps produce a useful error."""

    try:
        loader = _LOADERS[config.engine]
    except KeyError as e:
        raise ConfigError(f"no connector registered for engine: {config.engine}") from e
    try:
        cls = loader()
    except ImportError as e:
        raise ConfigError(
            f"driver for {config.engine.value} is not installed. "
            f"Install with: pip install 'touchstone-core[{config.engine.value}]'. "
            f"Original error: {e}"
        ) from e
    return cls(config)
