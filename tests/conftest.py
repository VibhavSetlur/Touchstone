"""Shared pytest fixtures for the Touchstone test suite."""

from __future__ import annotations

import pytest

from touchstone.config import Config, ConnectionConfig, SecurityConfig
from touchstone.security import (
    AuditLogger,
    ConsentGate,
    Gateway,
    Masker,
    PIIDetector,
    PolicyEngine,
    RateLimiter,
)
from touchstone.security.audit import MemorySink
from touchstone.security.consent import AlwaysApproveChannel
from touchstone.types import Engine


@pytest.fixture
def duckdb_path(tmp_path):
    return str(tmp_path / "test.duckdb")


@pytest.fixture
def basic_config(duckdb_path):
    return Config(
        connections={
            "test-duck": ConnectionConfig(
                name="test-duck", engine=Engine.DUCKDB, database=duckdb_path,
                read_only=True, tags=["dev"],
            ),
        },
        security=SecurityConfig(
            policy_files=[], pii_threshold=0.4,
            audit_sinks=[{"kind": "memory"}],
        ),
    )


@pytest.fixture
def gateway(basic_config):
    sink = MemorySink()
    return Gateway(
        config=basic_config,
        policy=PolicyEngine.from_files([]),
        pii=PIIDetector(threshold=0.4, enabled=["column_name", "regex"]),
        masker=Masker(default_strategy="redact"),
        consent=ConsentGate(channel=AlwaysApproveChannel()),
        rate_limiter=RateLimiter(per_minute=600),
        audit=AuditLogger(sinks=[sink]),
    ), sink


@pytest.fixture
def seeded_duckdb(duckdb_path):
    """A DuckDB file with a customers table that has some PII to detect."""
    import duckdb
    conn = duckdb.connect(duckdb_path)
    conn.execute("""
        CREATE TABLE customers (
            customer_id BIGINT PRIMARY KEY,
            email VARCHAR,
            full_name VARCHAR,
            phone VARCHAR,
            tier VARCHAR
        )
    """)
    conn.execute("""
        INSERT INTO customers VALUES
            (1, 'jane@example.com', 'Jane Doe',   '+15551112233', 'gold'),
            (2, 'john@example.com', 'John Smith', '+15552223344', 'standard'),
            (3, 'alice@example.com','Alice Liu',  '+15553334455', 'standard'),
            (4, 'bob@example.com',  'Bob Chen',   NULL,           'platinum')
    """)
    conn.close()
    return duckdb_path
