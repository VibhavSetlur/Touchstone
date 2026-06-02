"""Web automation — browse, log in, extract, verify.

CRITICAL security properties:
  - The AI assistant NEVER sees credentials. It passes a `CredentialRef`
    name; the secret is resolved server-side, used, scrubbed.
  - MFA is solved via `bootstrap_session`: a one-time interactive flow
    that captures cookies + localStorage, encrypts them with an
    operator-controlled key, and stores them for headless reuse.
  - Navigation is allowlisted by origin; mid-session redirects to a login
    page raise `SessionExpiredError` and refuse further work until
    re-bootstrap.
  - For dynamic / virtualized / canvas-rendered dashboards, prefer the
    BI tool's native CSV export (`bi_export` step) over DOM scraping —
    it uses the same authenticated session and returns ground-truth data.
"""

from touchstone.web.bootstrap import BootstrapResult, bootstrap_session
from touchstone.web.browser import (
    BrowserSession,
    BrowserStep,
    BrowserStepResult,
    CredentialRef,
    SessionExpiredError,
)
from touchstone.web.exporters import EXPORTERS, export_via_browser
from touchstone.web.extractor import extract_table, extract_text
from touchstone.web.session_store import (
    SessionStore,
    SessionStoreError,
    looks_like_login_page,
    looks_like_mfa_challenge,
)
from touchstone.web.verifier import verify_rendered_against_db

__all__ = [
    "BootstrapResult",
    "BrowserSession",
    "BrowserStep",
    "BrowserStepResult",
    "CredentialRef",
    "EXPORTERS",
    "SessionExpiredError",
    "SessionStore",
    "SessionStoreError",
    "bootstrap_session",
    "export_via_browser",
    "extract_table",
    "extract_text",
    "looks_like_login_page",
    "looks_like_mfa_challenge",
    "verify_rendered_against_db",
]
