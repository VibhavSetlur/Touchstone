"""PII detector tests."""

from __future__ import annotations

from touchstone.security.pii import (
    CreditCard,
    EmailDetector,
    PIIDetector,
    USSSNDetector,
    _luhn,
)
from touchstone.types import Column, Engine, QueryResult


def test_email_detector_finds_email():
    assert EmailDetector().confidence("jane@example.com") >= 0.9
    assert EmailDetector().confidence("not an email") == 0.0


def test_ssn_detector():
    assert USSSNDetector().confidence("My SSN is 123-45-6789.") >= 0.9
    assert USSSNDetector().confidence("12345-6789") == 0.0


def test_credit_card_luhn():
    assert _luhn("4242424242424242")
    assert not _luhn("4242424242424241")
    assert CreditCard().confidence("Pay with 4242 4242 4242 4242") >= 0.9
    assert CreditCard().confidence("Order #1234567890123456") == 0.0  # not Luhn-valid


def test_column_name_heuristic_flags_email_column():
    detector = PIIDetector(threshold=0.4, enabled=["column_name"])
    result = QueryResult(
        columns=[Column(name="email", type="varchar"),
                 Column(name="other", type="varchar")],
        rows=[("hidden", "anything"), ("also hidden", "thing 2")],
        row_count=2, engine=Engine.DUCKDB,
    )
    findings = detector.scan(result)
    by_col = {f.column for f in findings}
    assert "email" in by_col
    assert "other" not in by_col


def test_regex_detector_finds_email_in_value():
    detector = PIIDetector(threshold=0.4, enabled=["email"])
    result = QueryResult(
        columns=[Column(name="note", type="text")],
        rows=[("contact: jane@example.com",), ("nothing here",)],
        row_count=2, engine=Engine.DUCKDB,
    )
    findings = detector.scan(result)
    assert any(f.row_index == 0 for f in findings)
    assert not any(f.row_index == 1 for f in findings)
