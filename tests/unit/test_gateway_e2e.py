"""End-to-end gateway test: against an in-process DuckDB, run a query and
verify PII masking + audit recording."""

from __future__ import annotations

import pytest

from touchstone.security.gateway import ToolCallContext
from touchstone.types import PolicyDeniedError


@pytest.mark.usefixtures("seeded_duckdb")
def test_query_masks_email_column(gateway):
    gw, _sink = gateway
    out = gw.execute(ToolCallContext(
        assistant_id="t", assistant_session="s",
        tool="query_database", connection="test-duck",
        sql="SELECT customer_id, email, full_name FROM customers",
    ))
    rows = out.masked.result.to_dicts()
    assert len(rows) == 4
    for row in rows:
        assert row["email"] == "[REDACTED:EMAIL]"
        assert row["customer_id"] in (1, 2, 3, 4)


@pytest.mark.usefixtures("seeded_duckdb")
def test_audit_record_written(gateway):
    gw, sink = gateway
    gw.execute(ToolCallContext(
        assistant_id="t", assistant_session="s",
        tool="query_database", connection="test-duck",
        sql="SELECT * FROM customers",
    ))
    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec.tool == "query_database"
    assert rec.rows == 4
    assert rec.pii_summary  # something was found


@pytest.mark.usefixtures("seeded_duckdb")
def test_password_hash_touch_denied(gateway):
    gw, _ = gateway
    with pytest.raises(PolicyDeniedError):
        gw.execute(ToolCallContext(
            assistant_id="t", assistant_session="s",
            tool="query_database", connection="test-duck",
            # Cooked-up reference to a forbidden table.
            sql="SELECT * FROM users.password_hash",
        ))
