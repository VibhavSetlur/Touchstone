"""Per-call identity propagation for the MCP server.

For stdio transport, the identity is whatever the operator set via env vars
at process start. For Streamable HTTP behind an OAuth proxy, each request
carries its own subject — we capture it in a contextvar so the per-tool
`_aid() / _sid() / _tid()` helpers see the right caller.

This avoids the trap where a multi-user MCP server uses a process-level env
var and bleeds identity across requests.
"""

from __future__ import annotations

from contextvars import ContextVar


_assistant_id: ContextVar[str | None] = ContextVar("touchstone_assistant_id", default=None)
_session_id: ContextVar[str | None] = ContextVar("touchstone_session_id", default=None)
_tenant_id: ContextVar[str | None] = ContextVar("touchstone_tenant_id", default=None)


def set_identity(*, assistant_id: str | None = None,
                 session_id: str | None = None,
                 tenant_id: str | None = None):
    """Set per-call identity. Returns a token list — pass to `reset_identity`
    to restore the previous state (typically in a middleware finally block)."""
    tokens = []
    if assistant_id is not None:
        tokens.append(_assistant_id.set(assistant_id))
    if session_id is not None:
        tokens.append(_session_id.set(session_id))
    if tenant_id is not None:
        tokens.append(_tenant_id.set(tenant_id))
    return tokens


def reset_identity(tokens) -> None:
    for var, token in zip([_tenant_id, _session_id, _assistant_id], reversed(tokens),
                           strict=False):
        try:
            var.reset(token)
        except (LookupError, ValueError):
            pass


def current_identity() -> str | None:
    return _assistant_id.get()


def current_session() -> str | None:
    return _session_id.get()


def current_tenant() -> str | None:
    return _tenant_id.get()
