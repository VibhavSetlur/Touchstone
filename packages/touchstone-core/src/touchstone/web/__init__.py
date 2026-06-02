"""Web automation — browse, log in, click, extract.

CRITICAL: The AI assistant NEVER sees credentials. The browser session is
constructed in the gateway and credentials are injected by name via the
secret resolver. The AI passes a `CredentialRef` name (e.g. `looker_admin`)
and Touchstone resolves it to the actual secret without crossing the MCP
boundary.

All browser activity passes through `web.gateway.execute_step()`, which:
  - logs every navigation / click / fill to the audit log;
  - rejects any value that looks like an embedded credential leaking back;
  - PII-scans any extracted page content before returning;
  - enforces per-session timeouts and rate limits.
"""

from touchstone.web.browser import (
    BrowserSession,
    BrowserStep,
    BrowserStepResult,
    CredentialRef,
)
from touchstone.web.extractor import extract_table, extract_text
from touchstone.web.verifier import verify_rendered_against_db

__all__ = [
    "BrowserSession",
    "BrowserStep",
    "BrowserStepResult",
    "CredentialRef",
    "extract_table",
    "extract_text",
    "verify_rendered_against_db",
]
