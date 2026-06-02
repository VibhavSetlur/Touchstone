"""Cost-guard tests."""

from __future__ import annotations

import pytest

from touchstone.security.cost_guard import (
    ConcurrencyCapError,
    ConcurrencyGate,
    CostGuard,
    CostGuardError,
    CostLimits,
)
from touchstone.types import Engine


def test_refuses_cross_join():
    g = CostGuard(limits=CostLimits())
    with pytest.raises(CostGuardError):
        g.static_check("SELECT * FROM a CROSS JOIN b LIMIT 10",
                        Engine.DUCKDB, [])


def test_refuses_join_without_predicate():
    g = CostGuard(limits=CostLimits())
    # No ON or USING → treated as cross-join.
    with pytest.raises(CostGuardError):
        g.static_check("SELECT * FROM a JOIN b LIMIT 10",
                        Engine.DUCKDB, [])


def test_auto_injects_limit_on_unbounded_select():
    g = CostGuard(limits=CostLimits(auto_inject_limit=500))
    sql, warnings = g.static_check("SELECT * FROM orders",
                                     Engine.DUCKDB, [])
    assert "LIMIT 500" in sql
    assert any("auto-injected" in w for w in warnings)


def test_does_not_inject_limit_on_aggregate():
    g = CostGuard(limits=CostLimits(auto_inject_limit=500))
    sql, warnings = g.static_check("SELECT COUNT(*) FROM orders",
                                     Engine.DUCKDB, [])
    assert "LIMIT" not in sql.upper().replace("LIMIT 0", "")  # no inject
    assert not any("auto-injected" in w for w in warnings)


def test_large_table_select_star_without_limit_refused():
    g = CostGuard(limits=CostLimits(auto_inject_limit=0),
                   large_tables={"events_5b"})
    with pytest.raises(CostGuardError):
        g.static_check("SELECT * FROM events_5b", Engine.DUCKDB, [])


def test_concurrency_gate():
    gate = ConcurrencyGate(cap=2)
    gate.acquire("a")
    gate.acquire("a")
    with pytest.raises(ConcurrencyCapError):
        gate.acquire("a")
    gate.release("a")
    gate.acquire("a")  # back under cap → ok
