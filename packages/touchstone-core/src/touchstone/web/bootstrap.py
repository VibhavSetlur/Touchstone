"""Interactive session bootstrap.

Opens a HEADED browser, lets a human complete login (including MFA/SSO),
captures the resulting Playwright storage_state, encrypts it, and stores
it for headless reuse.

This is the only Touchstone flow that ever sees a UI — every other flow is
headless. The AI assistant cannot trigger bootstrap; it's an operator-only
CLI command. The AI can, however, ask "is the session for X still valid?"
via an MCP tool and the operator decides whether to re-bootstrap.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from touchstone.secrets import resolve
from touchstone.web.session_store import (
    SessionStore,
    SessionStoreError,
    looks_like_login_page,
)


@dataclass(slots=True)
class BootstrapResult:
    credential_name: str
    final_url: str
    session_path: Path
    captured_cookies: int
    captured_origins: int


def bootstrap_session(
    *,
    credential_name: str,
    credential_config: dict[str, str],
    base_dir: str | Path | None = None,
    headless: bool = False,
    timeout_minutes: int = 10,
    on_prompt=None,   # callable(msg: str) -> None — for the CLI/UI to render
    on_wait=None,     # callable() -> None — called after login URL load; blocks until human signals "done"
) -> BootstrapResult:
    """Run the bootstrap flow.

    The default `on_wait` reads from stdin (CLI use). The Triage UI swaps in
    a websocket-backed wait so the human can press a button instead.
    """
    if on_prompt is None:
        def on_prompt(msg: str) -> None:
            print(msg, file=sys.stderr, flush=True)
    if on_wait is None:
        def on_wait() -> None:
            try:
                input("\nPress ENTER once you've completed login (including MFA): ")
            except (EOFError, KeyboardInterrupt):
                raise SessionStoreError("bootstrap cancelled") from None

    login_url = credential_config.get("login_url")
    if not login_url:
        raise SessionStoreError(
            f"credential {credential_name!r} has no `login_url` configured. "
            "Add it under [web.credentials.<name>] so bootstrap knows where to start."
        )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise SessionStoreError(
            "playwright not installed. pip install 'touchstone-core[web]' "
            "&& playwright install chromium"
        ) from e

    on_prompt(f"\nOpening browser for credential {credential_name!r}...")
    on_prompt(f"  Login URL: {login_url}")
    on_prompt("  Complete login (including MFA push, TOTP, SSO redirects).")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(login_url, wait_until="domcontentloaded",
                  timeout=timeout_minutes * 60 * 1000)

        on_wait()

        final_url = page.url
        title = page.title()
        if looks_like_login_page(final_url, title):
            on_prompt(
                "WARNING: the page still looks like a login page. "
                "Saving session anyway, but it may not be authenticated."
            )

        state = context.storage_state()
        cookies = len(state.get("cookies") or [])
        origins = len(state.get("origins") or [])

        store = SessionStore(base_dir=base_dir)
        path = store.save(
            credential_name=credential_name,
            storage_state=state,
            fingerprint=_fingerprint(login_url, page.evaluate("navigator.userAgent")),
        )

        browser.close()

    on_prompt(f"\nSaved encrypted session to {path}")
    on_prompt(f"  cookies: {cookies}   origins: {origins}")
    on_prompt("  Touchstone headless sessions will now reuse this until it expires.")

    return BootstrapResult(
        credential_name=credential_name, final_url=final_url,
        session_path=path, captured_cookies=cookies, captured_origins=origins,
    )


def _fingerprint(login_url: str, user_agent: str) -> str:
    import hashlib
    return hashlib.sha256(f"{login_url}|{user_agent}".encode()).hexdigest()[:16]
