"""Snapshot SQL rewriting tests."""

from __future__ import annotations

from touchstone.security.snapshot import Snapshot, rewrite_sql_with_time_travel
from touchstone.types import Engine


def test_snowflake_time_travel_injected():
    snap = Snapshot(engine=Engine.SNOWFLAKE,
                     snapshot_ts="2026-06-02T19:00:00+00:00",
                     time_travel_clause="AT(TIMESTAMP => '2026-06-02T19:00:00+00:00'::TIMESTAMP_TZ)")
    sql = "SELECT * FROM orders WHERE total > 0"
    rewritten = rewrite_sql_with_time_travel(sql, Engine.SNOWFLAKE, snap)
    assert "AT(TIMESTAMP =>" in rewritten


def test_bigquery_time_travel_injected():
    snap = Snapshot(engine=Engine.BIGQUERY,
                     snapshot_ts="2026-06-02T19:00:00+00:00",
                     time_travel_clause="FOR SYSTEM_TIME AS OF TIMESTAMP('2026-06-02T19:00:00+00:00')")
    rewritten = rewrite_sql_with_time_travel(
        "SELECT day, total FROM marts.daily_rev",
        Engine.BIGQUERY, snap,
    )
    assert "FOR SYSTEM_TIME AS OF" in rewritten


def test_no_clause_means_no_rewrite():
    snap = Snapshot(engine=Engine.POSTGRES,
                     snapshot_ts="2026-06-02T19:00:00+00:00",
                     transaction_id="pg_rr", time_travel_clause=None)
    sql = "SELECT * FROM orders"
    assert rewrite_sql_with_time_travel(sql, Engine.POSTGRES, snap) == sql
