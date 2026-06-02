"""Masker tests."""

from __future__ import annotations

from touchstone.security.masker import Masker
from touchstone.types import Column, Engine, PIIFinding, QueryResult


def _result_with_email():
    return QueryResult(
        columns=[Column(name="email", type="varchar"),
                 Column(name="name", type="varchar")],
        rows=[("jane@example.com", "Jane"), ("john@example.com", "John")],
        row_count=2, engine=Engine.DUCKDB,
    )


def test_redact_strategy_replaces_value():
    r = _result_with_email()
    findings = [
        PIIFinding(column="email", row_index=0, detector="x",
                   entity_type="EMAIL", confidence=0.9),
        PIIFinding(column="email", row_index=1, detector="x",
                   entity_type="EMAIL", confidence=0.9),
    ]
    masked = Masker(default_strategy="redact").apply(r, findings)
    assert masked.result.rows[0][0] == "[REDACTED:EMAIL]"
    assert masked.result.rows[1][0] == "[REDACTED:EMAIL]"
    # untouched cells unchanged
    assert masked.result.rows[0][1] == "Jane"


def test_hash_strategy_is_stable():
    r = _result_with_email()
    findings = [
        PIIFinding(column="email", row_index=0, detector="x",
                   entity_type="EMAIL", confidence=0.9),
    ]
    masked1 = Masker(default_strategy="hash").apply(r, findings)
    masked2 = Masker(default_strategy="hash").apply(r, findings)
    assert masked1.result.rows[0][0] == masked2.result.rows[0][0]


def test_per_column_override_wins():
    r = _result_with_email()
    findings = [
        PIIFinding(column="email", row_index=0, detector="x",
                   entity_type="EMAIL", confidence=0.9),
    ]
    masker = Masker(default_strategy="redact",
                    per_column={"email": "tokenize"})
    masked = masker.apply(r, findings)
    assert masked.result.rows[0][0].startswith("EMAIL_")
