"""Gateway construction — used by CLI and MCP server."""

from __future__ import annotations

import os

from touchstone import Config
from touchstone.security import (
    AuditLogger,
    ConnectorPool,
    ConsentGate,
    CostGuard,
    CostLimits,
    Gateway,
    Masker,
    PIIDetector,
    PolicyEngine,
    RateLimiter,
    SensitivityRegistry,
    SnapshotManager,
    TenantRegistry,
)
from touchstone.security.consent import TerminalChannel, WebhookChannel


def build_gateway(config: Config) -> Gateway:
    policy = PolicyEngine.from_files(config.security.policy_files)
    pii = PIIDetector(
        threshold=config.security.pii_threshold,
        enabled=config.security.pii_detectors_enabled,
    )
    masker = Masker(default_strategy=config.security.pii_default_strategy)
    rate_limiter = RateLimiter(per_minute=config.security.rate_limit_per_minute)
    audit = AuditLogger.from_config(config.security.audit_sinks)
    consent = ConsentGate(
        channel=(
            WebhookChannel(os.environ["TOUCHSTONE_CONSENT_WEBHOOK"])
            if "TOUCHSTONE_CONSENT_WEBHOOK" in os.environ
            else TerminalChannel()
        ),
    )

    cost_limits = CostLimits(
        max_estimated_rows=config.cost.max_estimated_rows,
        max_estimated_bytes=config.cost.max_estimated_bytes,
        refuse_cross_join=config.cost.refuse_cross_join,
        require_limit_on_large=config.cost.require_limit_on_large,
        auto_inject_limit=config.cost.auto_inject_limit,
        concurrent_cap_per_assistant=config.cost.concurrent_cap_per_assistant,
    )
    cost_guard = CostGuard(limits=cost_limits, large_tables=set(config.cost.large_tables))

    sensitivity = SensitivityRegistry.from_config(config.sensitivity)
    tenants = TenantRegistry.from_config(config.tenants)
    pool = ConnectorPool()
    snapshots = SnapshotManager()

    return Gateway(
        config=config, policy=policy, pii=pii, masker=masker,
        consent=consent, rate_limiter=rate_limiter, audit=audit,
        cost_guard=cost_guard, sensitivity=sensitivity,
        tenants=tenants, connector_pool=pool, snapshot_manager=snapshots,
    )
