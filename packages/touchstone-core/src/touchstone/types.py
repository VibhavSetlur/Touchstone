"""Shared types across Touchstone.

We define these in one place because the trust boundary depends on stable,
typed contracts between layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Engine(str, Enum):
    """Database engine identifier. Used by the connector registry and policy engine."""

    POSTGRES = "postgres"
    MYSQL = "mysql"
    SNOWFLAKE = "snowflake"
    BIGQUERY = "bigquery"
    DATABRICKS = "databricks"
    REDSHIFT = "redshift"
    MONGODB = "mongodb"
    TRINO = "trino"
    CLICKHOUSE = "clickhouse"
    DUCKDB = "duckdb"
    SQLITE = "sqlite"
    FILES = "files"


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type: str  # connector-native type string; profiler maps to semantic type
    nullable: bool = True
    primary_key: bool = False
    description: str | None = None


@dataclass(frozen=True, slots=True)
class TableRef:
    """A fully-qualified table reference. `database` and `schema` are optional
    because some engines (SQLite, DuckDB) have no schema concept."""

    name: str
    schema: str | None = None
    database: str | None = None

    def qualified(self) -> str:
        parts = [p for p in (self.database, self.schema, self.name) if p]
        return ".".join(parts)

    def __str__(self) -> str:
        return self.qualified()


@dataclass(slots=True)
class QueryResult:
    """A connector query result, pre-masking. Never returned directly to the
    LLM — must pass through the masker first."""

    columns: list[Column]
    rows: list[tuple[Any, ...]]
    row_count: int
    truncated: bool = False
    bytes_returned: int = 0
    latency_ms: float = 0.0
    engine: Engine = Engine.DUCKDB
    sql_executed: str = ""

    def to_dicts(self) -> list[dict[str, Any]]:
        return [dict(zip([c.name for c in self.columns], row, strict=False)) for row in self.rows]


class Verdict(str, Enum):
    PERMIT = "permit"
    DENY = "deny"
    CONSENT_REQUIRED = "consent_required"


@dataclass(slots=True)
class PolicyDecision:
    verdict: Verdict
    matched_rule: str | None = None
    reason: str | None = None
    consent_context: dict[str, Any] | None = None


@dataclass(slots=True)
class PIIFinding:
    column: str
    row_index: int
    detector: str
    entity_type: str
    confidence: float
    span: tuple[int, int] | None = None  # char span within the value, if any


@dataclass(slots=True)
class AuditRecord:
    """One immutable record in the append-only audit log."""

    ts: datetime
    assistant_id: str
    assistant_session: str
    tool: str
    connection: str
    sql_hash: str
    sql_ast_summary: dict[str, Any]
    policy_verdict: PolicyDecision
    pii_summary: dict[str, int] = field(default_factory=dict)
    rows: int = 0
    cells: int = 0
    bytes_returned: int = 0
    latency_ms: float = 0.0
    sample_masked: list[dict[str, Any]] = field(default_factory=list)
    prev_record_hash: str = ""
    record_hash: str = ""


class TouchstoneError(Exception):
    """Base for all Touchstone errors. Errors are designed to be informative to
    an LLM caller — the message should explain what to do next, not just what
    went wrong."""

    code: str = "touchstone/unknown"


class PolicyDeniedError(TouchstoneError):
    code = "policy/denied"


class ConsentRequiredError(TouchstoneError):
    code = "consent/required"


class ConnectorError(TouchstoneError):
    code = "connector/error"


class ConnectorTimeoutError(ConnectorError):
    code = "connector/timeout"


class ConnectorAuthError(ConnectorError):
    code = "connector/auth"


class PIIRefusedError(TouchstoneError):
    code = "pii/refused"


class RateLimitedError(TouchstoneError):
    code = "rate_limited"


class ConfigError(TouchstoneError):
    code = "config/invalid"
