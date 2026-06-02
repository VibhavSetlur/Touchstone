"""Security layer — the trust boundary.

QA capabilities, the MCP server, the CLI, and the GitHub App MUST go through
`security.gateway.execute(...)` for any operation that reaches a connector.
A Ruff custom rule and an integration test enforce that no code outside this
package imports from `touchstone.connectors`.
"""

from touchstone.security.audit import AuditLogger
from touchstone.security.consent import ConsentGate
from touchstone.security.cost_guard import (
    ConcurrencyCapError,
    CostGuard,
    CostGuardError,
    CostLimits,
)
from touchstone.security.gateway import Gateway, GatewayOutcome, ToolCallContext
from touchstone.security.masker import Masker
from touchstone.security.pii import PIIDetector
from touchstone.security.policy import PolicyEngine
from touchstone.security.rate_limit import RateLimiter
from touchstone.security.sensitivity import SensitivityCatalog, SensitivityRegistry
from touchstone.security.snapshot import Snapshot, SnapshotManager
from touchstone.security.tenant import (
    DEFAULT_TENANT,
    ConnectorPool,
    TenantManifest,
    TenantRegistry,
)

__all__ = [
    "AuditLogger",
    "ConcurrencyCapError",
    "ConnectorPool",
    "ConsentGate",
    "CostGuard",
    "CostGuardError",
    "CostLimits",
    "DEFAULT_TENANT",
    "Gateway",
    "GatewayOutcome",
    "Masker",
    "PIIDetector",
    "PolicyEngine",
    "RateLimiter",
    "SensitivityCatalog",
    "SensitivityRegistry",
    "Snapshot",
    "SnapshotManager",
    "TenantManifest",
    "TenantRegistry",
    "ToolCallContext",
]
