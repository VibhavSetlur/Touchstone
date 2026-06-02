"""Files connector tests — CSV/Excel/Parquet via DuckDB."""

from __future__ import annotations

from pathlib import Path

import pytest

from touchstone.config import ConnectionConfig
from touchstone.connectors.files import FilesConnector
from touchstone.types import Engine


@pytest.fixture
def csv_dir(tmp_path: Path):
    (tmp_path / "orders.csv").write_text(
        "order_id,total,currency\n"
        "1,99.50,USD\n"
        "2,15.00,EUR\n"
        "3,250.00,GBP\n"
    )
    (tmp_path / "customers.csv").write_text(
        "id,email,name\n"
        "1,a@example.com,Alice\n"
        "2,b@example.com,Bob\n"
    )
    return tmp_path


def test_files_auto_registers_each_csv(csv_dir: Path):
    cfg = ConnectionConfig(
        name="t", engine=Engine.FILES, database=str(csv_dir),
        read_only=True, tags=["dev"],
    )
    with FilesConnector(cfg) as c:
        names = {t.name for t in c.list_tables()}
        assert {"orders", "customers"} <= names

        r = c.execute("SELECT COUNT(*) FROM orders")
        assert r.rows[0][0] == 3

        r = c.execute("SELECT currency, total FROM orders ORDER BY total")
        assert r.rows[0][0] == "EUR"


def test_files_explicit_view_mapping(tmp_path: Path):
    (tmp_path / "weird.csv").write_text("a,b\n1,2\n")
    cfg = ConnectionConfig(
        name="t", engine=Engine.FILES, database=str(tmp_path),
        read_only=True, tags=["dev"],
        extra={"views": {"things": str(tmp_path / "weird.csv")}},
    )
    with FilesConnector(cfg) as c:
        names = {t.name for t in c.list_tables()}
        assert "things" in names
