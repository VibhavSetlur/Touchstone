"""Per-tenant isolation.

In a shared-deployment scenario (one MCP server serving multiple
teams/clients), tenant isolation MUST be structural — not a runtime check
the LLM could route around.

Design:

  1. Every `ToolCallContext` carries a `tenant_id`. It's resolved from the
     MCP transport's authenticated identity (OAuth subject, mTLS CN, etc.),
     not from anything the AI controls. The AI has no MCP tool to set it.

  2. Connectors are partitioned per (tenant_id, connection_name). A
     connector instance is never reused across tenants. The
     ConnectorPool below holds connectors in a per-tenant dict; if tenant A
     opens `prod-ro`, tenant B requesting `prod-ro` gets a fresh, separate
     connector — different driver instance, different session state.

  3. Connection configs are filtered per-tenant at load time. Tenant A's
     manifest never mentions tenant B's connections, so the AI cannot even
     name a wrong-tenant connection.

  4. Audit records carry `tenant_id`. Tampering with the chain across
     tenants is detectable.

  5. Policy evaluations carry `tenant_id` in context, so policy rules can
     scope by tenant.

  6. The trust-boundary test asserts that no code path reads the tenant
     from anywhere except the gateway's `ToolCallContext`.

For a single-tenant deployment, every call uses `tenant_id = "default"`.
The mechanism is the same; it just collapses to one bucket.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


DEFAULT_TENANT = "default"


@dataclass(slots=True)
class TenantManifest:
    """What this tenant can see / do.

    `connections` is the subset of the global config's connections this
    tenant is allowed to address. `policy_files` are tenant-scoped policy
    overrides (loaded after the global default bundle).
    """

    tenant_id: str
    connections: set[str] = field(default_factory=set)
    policy_files: list[str] = field(default_factory=list)
    consent_channel: str | None = None
    cost_limits: dict[str, Any] = field(default_factory=dict)


class TenantRegistry:
    """Static mapping of tenant_id → TenantManifest."""

    def __init__(self, manifests: dict[str, TenantManifest] | None = None,
                 default: TenantManifest | None = None) -> None:
        self._manifests = manifests or {}
        self._default = default or TenantManifest(tenant_id=DEFAULT_TENANT)

    def get(self, tenant_id: str) -> TenantManifest:
        return self._manifests.get(tenant_id, self._default)

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> TenantRegistry:
        manifests = {}
        for tid, spec in raw.items():
            manifests[tid] = TenantManifest(
                tenant_id=tid,
                connections=set(spec.get("connections", []) or []),
                policy_files=spec.get("policy_files", []) or [],
                consent_channel=spec.get("consent_channel"),
                cost_limits=spec.get("cost_limits", {}) or {},
            )
        return cls(manifests=manifests)


class ConnectorPool:
    """Per-tenant connector cache.

    Each (tenant_id, connection_name) tuple gets its own connector instance.
    Connectors are created on first use and reused for subsequent calls from
    the same tenant. Other tenants requesting the same connection name get
    their own separate instance.

    Pool is bounded — LRU eviction prevents unbounded growth in long-running
    deployments. Eviction closes the connector.
    """

    def __init__(self, max_per_tenant: int = 16) -> None:
        self._cache: dict[tuple[str, str], Any] = {}
        self._lru: dict[tuple[str, str], int] = {}
        self._tick = 0
        self._max_per_tenant = max_per_tenant
        self._lock = threading.Lock()

    def get(self, tenant_id: str, conn_cfg) -> Any:
        from touchstone.connectors import get_connector

        key = (tenant_id, conn_cfg.name)
        with self._lock:
            self._tick += 1
            cached = self._cache.get(key)
            if cached is not None:
                self._lru[key] = self._tick
                return cached
            # Evict LRU for this tenant if we're at cap.
            tenant_keys = [k for k in self._cache if k[0] == tenant_id]
            if len(tenant_keys) >= self._max_per_tenant:
                victim = min(tenant_keys, key=lambda k: self._lru.get(k, 0))
                self._close(victim)
            connector = get_connector(conn_cfg)
            connector.connect()
            self._cache[key] = connector
            self._lru[key] = self._tick
            return connector

    def evict_tenant(self, tenant_id: str) -> int:
        with self._lock:
            keys = [k for k in self._cache if k[0] == tenant_id]
            for k in keys:
                self._close(k)
            return len(keys)

    def _close(self, key: tuple[str, str]) -> None:
        connector = self._cache.pop(key, None)
        self._lru.pop(key, None)
        if connector is not None:
            try:
                connector.close()
            except Exception:
                pass

    def close_all(self) -> None:
        with self._lock:
            for key in list(self._cache):
                self._close(key)
