"""Connector base class.

Every connector implements the same interface so QA capabilities don't need
to know which engine they're talking to. Connectors are deliberately minimal:
they execute one query, return one result. Higher-level operations (profiling,
diffing) live in `touchstone.qa` and call the connector through the gateway.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from touchstone.config import ConnectionConfig
from touchstone.types import (
    Column,
    ConnectorError,
    ConnectorTimeoutError,
    Engine,
    QueryResult,
    TableRef,
)


class Connector(ABC):
    """Base for all database connectors.

    Implementations MUST:
      - Honor `config.timeout_seconds` server-side (vendor-specific).
      - Honor `config.row_cap` and `config.byte_cap` — truncate, set `truncated=True`.
      - Honor `config.read_only` — if True, set the session/transaction read-only
        at the engine level (not just trust the SQL allow-list).
      - Wrap engine errors in `ConnectorError` / `ConnectorTimeoutError` /
        `ConnectorAuthError` — never leak raw driver tracebacks to callers.
      - Scrub credentials from any error message before raising.
    """

    engine: Engine

    def __init__(self, config: ConnectionConfig) -> None:
        self.config = config
        self._connection: Any = None  # set by `connect()`

    @abstractmethod
    def connect(self) -> None:
        """Open the underlying connection. Idempotent."""

    @abstractmethod
    def close(self) -> None:
        """Close the underlying connection. Idempotent."""

    @abstractmethod
    def execute(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        """Execute one SQL statement and return a row-capped, byte-capped result.

        For non-SQL engines (Mongo), `sql` is a JSON-encoded operation spec.
        """

    @abstractmethod
    def list_tables(self, schema: str | None = None) -> list[TableRef]:
        """List tables visible to this connection. Used by the profiler and the
        lineage walker."""

    @abstractmethod
    def describe_table(self, table: TableRef) -> list[Column]:
        """Return the column list for a table."""

    def __enter__(self) -> Connector:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _timed[T](fn) -> Any:
    """Decorator: time a connector method and stamp `latency_ms` on its result."""

    def wrapper(self: Connector, *args: Any, **kwargs: Any) -> T:
        start = time.perf_counter()
        try:
            result = fn(self, *args, **kwargs)
        except ConnectorError:
            raise
        except TimeoutError as e:
            raise ConnectorTimeoutError(str(e)) from e
        if isinstance(result, QueryResult):
            result.latency_ms = (time.perf_counter() - start) * 1000.0
        return result

    return wrapper
