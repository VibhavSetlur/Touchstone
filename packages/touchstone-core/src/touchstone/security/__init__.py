"""Security layer — the trust boundary.

QA capabilities, the MCP server, the CLI, and the GitHub App MUST go through
`security.gateway.execute(...)` for any operation that reaches a connector.
A Ruff custom rule and an integration test enforce that no code outside this
package imports from `touchstone.connectors`.
"""

from touchstone.security.audit import AuditLogger
from touchstone.security.consent import ConsentGate
from touchstone.security.gateway import Gateway, ToolCallContext
from touchstone.security.masker import Masker
from touchstone.security.pii import PIIDetector
from touchstone.security.policy import PolicyEngine
from touchstone.security.rate_limit import RateLimiter

__all__ = [
    "AuditLogger",
    "ConsentGate",
    "Gateway",
    "Masker",
    "PIIDetector",
    "PolicyEngine",
    "RateLimiter",
    "ToolCallContext",
]
