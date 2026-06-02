"""Per-tenant isolation tests."""

from __future__ import annotations

import pytest

from touchstone.config import ConnectionConfig
from touchstone.security.tenant import ConnectorPool, TenantRegistry
from touchstone.types import Engine


def _cfg(name="c1"):
    return ConnectionConfig(name=name, engine=Engine.DUCKDB,
                             database=":memory:", read_only=False, tags=["dev"])


def test_pool_returns_different_instances_per_tenant():
    pool = ConnectorPool()
    cfg = _cfg()
    a = pool.get("tenant_a", cfg)
    b = pool.get("tenant_b", cfg)
    assert a is not b


def test_pool_reuses_within_tenant():
    pool = ConnectorPool()
    cfg = _cfg()
    a1 = pool.get("tenant_a", cfg)
    a2 = pool.get("tenant_a", cfg)
    assert a1 is a2


def test_evict_tenant_closes_connectors():
    pool = ConnectorPool()
    pool.get("tenant_a", _cfg("c1"))
    pool.get("tenant_a", _cfg("c2"))
    pool.get("tenant_b", _cfg("c1"))
    n = pool.evict_tenant("tenant_a")
    assert n == 2
    # tenant_b unaffected
    assert ("tenant_b", "c1") in pool._cache


def test_tenant_manifest_lookup():
    reg = TenantRegistry.from_config({
        "team-finance": {"connections": ["finance-ro", "warehouse-ro"]},
        "team-growth":  {"connections": ["growth-ro"]},
    })
    fm = reg.get("team-finance")
    assert "finance-ro" in fm.connections
    assert "growth-ro" not in fm.connections
    # Unknown tenant falls back to default (empty manifest).
    assert reg.get("unknown").tenant_id == "default"
