"""PR-impact analysis tests."""

from __future__ import annotations

from touchstone.qa.pr import FileChange, analyze_pr_data_impact


def test_detects_added_column():
    before = "CREATE TABLE orders (order_id BIGINT, total NUMERIC);"
    after = "CREATE TABLE orders (order_id BIGINT, total NUMERIC, discount_pct NUMERIC);"
    report = analyze_pr_data_impact(
        changes=[FileChange(path="schema.sql", before_sql=before, after_sql=after)],
    )
    added = [c for c in report.columns if c.kind == "added"]
    assert any(c.column == "discount_pct" for c in added)


def test_detects_dropped_table():
    before = "CREATE TABLE legacy_users (id BIGINT);"
    after = ""
    report = analyze_pr_data_impact(
        changes=[FileChange(path="m.sql", before_sql=before, after_sql=after)],
    )
    assert any(t.kind == "dropped" and t.name == "legacy_users" for t in report.tables)


def test_records_parse_failures_gracefully():
    report = analyze_pr_data_impact(
        changes=[FileChange(path="busted.sql",
                            before_sql="SELECT ;;;",
                            after_sql="SELECT ;;;")],
    )
    # sqlglot is lenient with a lot of malformed SQL — we don't assert a hard
    # failure, but we assert the analyzer doesn't blow up.
    assert isinstance(report.parse_failures, list)
