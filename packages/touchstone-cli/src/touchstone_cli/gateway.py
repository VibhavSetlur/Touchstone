"""Gateway construction for the CLI — same shape as the MCP server's."""

from __future__ import annotations

import os

from touchstone import Config
from touchstone.security import (
    AuditLogger,
    ConsentGate,
    Gateway,
    Masker,
    PIIDetector,
    PolicyEngine,
    RateLimiter,
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
    return Gateway(
        config=config, policy=policy, pii=pii, masker=masker,
        consent=consent, rate_limiter=rate_limiter, audit=audit,
    )
