"""Integration tests against a real Postgres (requires `docker compose up -d postgres`)."""

from __future__ import annotations

import os

import pytest

from touchstone.config import ConnectionConfig
from touchstone.connectors.postgres import PostgresConnector
from touchstone.types import Engine


pytestmark = pytest.mark.integration


@pytest.fixture
def pg_conn():
    if not os.environ.get("POSTGRES_PASSWORD"):
        pytest.skip("POSTGRES_PASSWORD not set")
    cfg = ConnectionConfig(
        name="t", engine=Engine.POSTGRES,
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ.get("POSTGRES_DB", "shop"),
        user=os.environ.get("POSTGRES_USER", "postgres"),
        password_ref="env://POSTGRES_PASSWORD",
        read_only=True,
    )
    with PostgresConnector(cfg) as c:
        yield c


def test_postgres_select(pg_conn):
    r = pg_conn.execute("SELECT 1 AS one")
    assert r.row_count == 1
    assert r.rows[0][0] == 1


def test_postgres_lists_tables(pg_conn):
    tables = pg_conn.list_tables()
    names = {t.name for t in tables}
    # init.sql creates these in the postgres-quickstart example
    assert "customers" in names
    assert "orders" in names


def test_postgres_row_cap(pg_conn):
    pg_conn.config.row_cap = 2
    r = pg_conn.execute("SELECT generate_series(1, 100) AS i")
    assert r.row_count == 2
    assert r.truncated is True
