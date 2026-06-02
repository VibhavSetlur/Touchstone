"""Policy engine tests — both the matcher and the safe-eval condition DSL."""

from __future__ import annotations

import pytest

from touchstone.config import ConnectionConfig
from touchstone.security.policy import PolicyEngine, PolicyRule, _evaluate_condition
from touchstone.types import Engine, Verdict


def _conn(name="c1", tags=None, ro=True):
    return ConnectionConfig(name=name, engine=Engine.DUCKDB, read_only=ro,
                            tags=tags or [])


def test_default_allows_select_on_readonly():
    pe = PolicyEngine.from_files([])
    decision = pe.evaluate(
        assistant_id="x", connection=_conn(ro=True),
        tool="query_database",
        sql_summary={"is_select": True, "kind": "SELECT", "touches": [], "joins": 0},
        metadata={},
    )
    assert decision.verdict == Verdict.PERMIT


def test_default_consent_on_non_select():
    pe = PolicyEngine.from_files([])
    decision = pe.evaluate(
        assistant_id="x", connection=_conn(ro=False),
        tool="query_database",
        sql_summary={"is_select": False, "kind": "INSERT", "touches": [], "joins": 0},
        metadata={},
    )
    assert decision.verdict == Verdict.CONSENT_REQUIRED


def test_default_consent_on_prod_tag():
    pe = PolicyEngine.from_files([])
    decision = pe.evaluate(
        assistant_id="x", connection=_conn(tags=["prod"]),
        tool="query_database",
        sql_summary={"is_select": True, "kind": "SELECT", "touches": [], "joins": 0},
        metadata={},
    )
    assert decision.verdict == Verdict.CONSENT_REQUIRED


def test_deny_on_password_hash_touch():
    pe = PolicyEngine.from_files([])
    decision = pe.evaluate(
        assistant_id="x", connection=_conn(),
        tool="query_database",
        sql_summary={"is_select": True, "kind": "SELECT",
                     "touches": ["users.password_hash"], "joins": 0},
        metadata={},
    )
    assert decision.verdict == Verdict.DENY


def test_condition_dsl_rejects_eval():
    ctx = {"sql": {"is_select": True}, "tags": set()}
    assert _evaluate_condition("sql.is_select", ctx)
    with pytest.raises(ValueError):
        _evaluate_condition("__import__('os').system('echo pwned')", ctx)


def test_condition_dsl_supports_membership():
    ctx = {"sql": {"touches": ["a", "b"]}, "tags": {"prod"}}
    assert _evaluate_condition('"a" in sql.touches', ctx)
    assert not _evaluate_condition('"c" in sql.touches', ctx)
    assert _evaluate_condition('"prod" in tags', ctx)
