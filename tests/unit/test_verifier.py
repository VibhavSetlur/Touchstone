"""Dashboard verifier comparison tests."""

from __future__ import annotations

from touchstone.web.verifier import _compare, _to_number


def test_exact_match():
    assert _compare(100, 100, 0.0) is None


def test_near_match_within_tolerance():
    assert _compare(100.0, 100.5, 0.01) is None
    assert _compare(100.0, 102.0, 0.01) == "exact_mismatch"


def test_k_suffix_detected_as_rounded():
    assert _compare("10.5K", 10500.56, 0.01) == "rounded_truncation"


def test_money_with_currency_parses():
    assert _to_number("$1,234.56") == 1234.56
    assert _to_number("€10,000") == 10000.0
    assert _to_number("not a number") is None


def test_string_compare_case_insensitive_strip():
    assert _compare("  USD  ", "usd", 0.0) is None
    assert _compare("USD", "EUR", 0.0) == "format_mismatch"


def test_none_vs_value():
    assert _compare(None, 0, 0.0) == "exact_mismatch"
    assert _compare(None, None, 0.0) is None
