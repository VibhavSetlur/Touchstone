"""Sensitivity catalog tests."""

from __future__ import annotations

from touchstone.security.sensitivity import SensitivityCatalog


def test_explicit_rules_match():
    cat = SensitivityCatalog.from_config({
        "redact": ["users.notes", "*.medical_*"],
        "never_leaves": ["medical.records.*"],
        "hash": ["users.email"],
    })
    assert cat.tier_for("users.notes", "TEXT", "long body of text...") == "redact"
    assert cat.tier_for("clinic.medical_history", "TEXT", "x") == "redact"
    assert cat.tier_for("medical.records.diagnosis", "TEXT", "x") == "never_leaves"
    assert cat.tier_for("users.email", "VARCHAR(255)", "x") == "hash"


def test_free_text_default_kicks_in_for_long_text_columns():
    cat = SensitivityCatalog(free_text_default_tier="redact")
    # TEXT type → flagged as free-text → redacted by default.
    assert cat.tier_for("support.body", "TEXT", "hello world") == "redact"


def test_free_text_default_does_not_apply_to_short_varchar():
    cat = SensitivityCatalog(free_text_default_tier="redact")
    assert cat.tier_for("users.tier", "VARCHAR(16)", "gold") == "allow"


def test_exempt_free_text_columns_pass_through():
    cat = SensitivityCatalog(
        free_text_default_tier="redact",
        exempt_free_text=["public.*"],
    )
    assert cat.tier_for("public.note", "TEXT", "hello") == "allow"
    assert cat.tier_for("private.note", "TEXT", "hello") == "redact"


def test_most_specific_rule_wins():
    cat = SensitivityCatalog.from_config({
        "redact": ["users.*"],
        "hash": ["users.email"],
    })
    assert cat.tier_for("users.email", "VARCHAR", "x") == "hash"
    assert cat.tier_for("users.phone", "VARCHAR", "x") == "redact"
