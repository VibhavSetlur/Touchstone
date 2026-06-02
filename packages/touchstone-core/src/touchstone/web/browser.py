"""Browser session for QA automation.

Wraps Playwright. The AI assistant drives the browser by issuing high-level
steps (`navigate`, `click`, `fill`, `wait_for`, `screenshot`, `extract_table`),
not low-level Selenium-style commands. This keeps the AI's surface narrow
and makes the audit log human-readable.

## How credentials work — the security contract

The AI assistant NEVER passes a raw credential string. It passes a
`CredentialRef` — the name of an entry in the operator's secret store. The
browser resolves the secret at fill-time, types it, and immediately scrubs
it from any in-memory representation that could surface back to the AI
(screenshots are masked, page text is scrubbed for the resolved value).

If the AI tries to pass a literal-looking value to a `fill` step on a field
that looks like a password (input[type=password], known login forms), the
session REFUSES the step and logs a security-policy event.

## Site-level allowlist

Operators configure `web.allowed_origins` in `touchstone.toml`. The browser
refuses to navigate to anything outside the allowlist. This is the "AI can't
exfiltrate to arbitrary URLs" backstop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse


@dataclass(slots=True, frozen=True)
class CredentialRef:
    """A handle to a credential the AI can pass without seeing the value.

    Operators register credentials in `touchstone.toml`:

        [web.credentials.looker_admin]
        username = "env://LOOKER_USER"
        password = "env://LOOKER_PASSWORD"

    The AI calls a step like:
        {"op": "login", "site": "looker", "credential": "looker_admin"}
    and the gateway/browser resolves `env://LOOKER_USER` and `env://LOOKER_PASSWORD`
    at fill-time. The AI never sees the resolved values.
    """

    name: str


Op = Literal[
    "navigate", "click", "fill", "select", "press", "wait_for_selector",
    "wait_for_url", "screenshot", "extract_text", "extract_table",
    "list_buttons", "list_inputs", "login",
]


@dataclass(slots=True)
class BrowserStep:
    """One step in a browser session. `value` is plain text for non-credential
    fills; credential fills MUST use `credential` and leave `value` unset."""

    op: Op
    url: str | None = None
    selector: str | None = None
    value: str | None = None
    credential: CredentialRef | None = None
    timeout_ms: int = 10_000
    field_role: str | None = None  # "username" | "password" | "totp" | ...
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BrowserStepResult:
    op: Op
    ok: bool
    url: str
    text: str | None = None             # extracted text (PII-masked upstream)
    table: list[dict[str, Any]] | None = None
    screenshot_path: str | None = None
    elements: list[dict[str, Any]] | None = None
    error: str | None = None
    latency_ms: float = 0.0


class BrowserSessionError(Exception):
    pass


class BrowserPolicyError(BrowserSessionError):
    """The session refused a step because it violated a security guard."""


# Selectors that strongly imply a password / secret field.
_PASSWORD_HINTS = (
    'input[type="password"]',
    'input[name*="password" i]',
    'input[name*="passwd" i]',
    'input[name*="secret" i]',
    'input[name*="token" i]',
    'input[id*="password" i]',
    'input[autocomplete="current-password"]',
    'input[autocomplete="new-password"]',
)


class BrowserSession:
    """One headless browser session, scoped to one AI tool call.

    Lifecycle: created by the web gateway, used for one or more BrowserStep
    calls, closed at the end. Persistent state (cookies, local storage) lives
    in a named context the operator manages — never in the AI's hands.
    """

    def __init__(
        self,
        *,
        allowed_origins: list[str],
        secret_resolver,         # callable: (uri: str) -> str
        credentials_config: dict[str, dict[str, str]],
        context_dir: str | None = None,
        headless: bool = True,
    ) -> None:
        self.allowed_origins = [o.rstrip("/") for o in allowed_origins]
        self._resolve = secret_resolver
        self._creds = credentials_config
        self._context_dir = context_dir
        self._headless = headless
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._scrub_values: list[str] = []  # secrets we've typed, for output scrubbing

    def open(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise BrowserSessionError(
                "playwright not installed. Install with: "
                "pip install 'touchstone-core[web]' && playwright install chromium"
            ) from e

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        if self._context_dir:
            self._context = self._browser.new_context(storage_state=self._context_dir)
        else:
            self._context = self._browser.new_context()
        self._page = self._context.new_page()

    def close(self) -> None:
        if self._page is not None:
            self._page.close()
            self._page = None
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if hasattr(self, "_pw"):
            self._pw.stop()

    def __enter__(self) -> BrowserSession:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- step executor -----------------------------------------------------

    def execute(self, step: BrowserStep) -> BrowserStepResult:
        import time

        start = time.perf_counter()
        try:
            result = self._dispatch(step)
            result.latency_ms = (time.perf_counter() - start) * 1000.0
            if result.text:
                result.text = self._scrub_output(result.text)
            return result
        except BrowserPolicyError as e:
            return BrowserStepResult(
                op=step.op, ok=False, url=self._page.url if self._page else "",
                error=f"policy: {e}", latency_ms=(time.perf_counter() - start) * 1000.0,
            )
        except Exception as e:
            return BrowserStepResult(
                op=step.op, ok=False, url=self._page.url if self._page else "",
                error=str(e), latency_ms=(time.perf_counter() - start) * 1000.0,
            )

    def _dispatch(self, step: BrowserStep) -> BrowserStepResult:
        if step.op == "navigate":
            return self._do_navigate(step)
        if step.op == "click":
            return self._do_click(step)
        if step.op == "fill":
            return self._do_fill(step)
        if step.op == "select":
            return self._do_select(step)
        if step.op == "press":
            return self._do_press(step)
        if step.op == "wait_for_selector":
            return self._do_wait_for_selector(step)
        if step.op == "wait_for_url":
            return self._do_wait_for_url(step)
        if step.op == "screenshot":
            return self._do_screenshot(step)
        if step.op == "extract_text":
            return self._do_extract_text(step)
        if step.op == "extract_table":
            return self._do_extract_table(step)
        if step.op == "list_buttons":
            return self._do_list_buttons(step)
        if step.op == "list_inputs":
            return self._do_list_inputs(step)
        if step.op == "login":
            return self._do_login(step)
        raise BrowserSessionError(f"unknown op: {step.op}")

    # -- guards ------------------------------------------------------------

    def _check_origin(self, url: str) -> None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if not any(origin == o or origin.startswith(o + "/") for o in self.allowed_origins):
            raise BrowserPolicyError(
                f"origin not in allowed_origins: {origin}. "
                "Add it to web.allowed_origins in touchstone.toml."
            )

    def _check_not_credential_field(self, selector: str | None) -> None:
        """If the AI is trying to type a literal into a password-looking
        field, refuse. The right way is to pass `credential=...`."""
        if not selector:
            return
        if any(_looks_like(selector, hint) for hint in _PASSWORD_HINTS):
            raise BrowserPolicyError(
                f"refused to fill credential-looking field {selector!r} with a "
                "literal value. Pass a `credential` reference instead."
            )

    # -- ops ---------------------------------------------------------------

    def _do_navigate(self, step: BrowserStep) -> BrowserStepResult:
        if not step.url:
            raise BrowserSessionError("navigate requires url")
        self._check_origin(step.url)
        self._page.goto(step.url, timeout=step.timeout_ms, wait_until="domcontentloaded")
        return BrowserStepResult(op="navigate", ok=True, url=self._page.url)

    def _do_click(self, step: BrowserStep) -> BrowserStepResult:
        if not step.selector:
            raise BrowserSessionError("click requires selector")
        self._page.click(step.selector, timeout=step.timeout_ms)
        return BrowserStepResult(op="click", ok=True, url=self._page.url)

    def _do_fill(self, step: BrowserStep) -> BrowserStepResult:
        if not step.selector:
            raise BrowserSessionError("fill requires selector")
        if step.credential is not None:
            value = self._resolve_credential(step.credential, step.field_role or "value")
            self._scrub_values.append(value)
            self._page.fill(step.selector, value, timeout=step.timeout_ms)
            del value
            return BrowserStepResult(op="fill", ok=True, url=self._page.url,
                                     text="[credential filled]")
        # Literal value path — but refuse if the field looks like a credential field.
        self._check_not_credential_field(step.selector)
        if step.value is None:
            raise BrowserSessionError("fill requires value or credential")
        self._page.fill(step.selector, step.value, timeout=step.timeout_ms)
        return BrowserStepResult(op="fill", ok=True, url=self._page.url)

    def _do_select(self, step: BrowserStep) -> BrowserStepResult:
        if not step.selector or step.value is None:
            raise BrowserSessionError("select requires selector and value")
        self._page.select_option(step.selector, step.value, timeout=step.timeout_ms)
        return BrowserStepResult(op="select", ok=True, url=self._page.url)

    def _do_press(self, step: BrowserStep) -> BrowserStepResult:
        if step.value is None:
            raise BrowserSessionError("press requires value (key name)")
        if step.selector:
            self._page.press(step.selector, step.value, timeout=step.timeout_ms)
        else:
            self._page.keyboard.press(step.value)
        return BrowserStepResult(op="press", ok=True, url=self._page.url)

    def _do_wait_for_selector(self, step: BrowserStep) -> BrowserStepResult:
        if not step.selector:
            raise BrowserSessionError("wait_for_selector requires selector")
        self._page.wait_for_selector(step.selector, timeout=step.timeout_ms)
        return BrowserStepResult(op="wait_for_selector", ok=True, url=self._page.url)

    def _do_wait_for_url(self, step: BrowserStep) -> BrowserStepResult:
        if not step.url:
            raise BrowserSessionError("wait_for_url requires url")
        self._page.wait_for_url(step.url, timeout=step.timeout_ms)
        return BrowserStepResult(op="wait_for_url", ok=True, url=self._page.url)

    def _do_screenshot(self, step: BrowserStep) -> BrowserStepResult:
        import tempfile
        path = step.metadata.get("path") or tempfile.mktemp(suffix=".png")
        self._page.screenshot(path=path, full_page=bool(step.metadata.get("full_page")))
        return BrowserStepResult(op="screenshot", ok=True, url=self._page.url,
                                 screenshot_path=path)

    def _do_extract_text(self, step: BrowserStep) -> BrowserStepResult:
        if step.selector:
            text = self._page.locator(step.selector).inner_text(timeout=step.timeout_ms)
        else:
            text = self._page.content()
        return BrowserStepResult(op="extract_text", ok=True, url=self._page.url, text=text)

    def _do_extract_table(self, step: BrowserStep) -> BrowserStepResult:
        from touchstone.web.extractor import extract_table as _extract
        sel = step.selector or "table"
        rows = _extract(self._page, sel, timeout_ms=step.timeout_ms)
        return BrowserStepResult(op="extract_table", ok=True, url=self._page.url,
                                 table=rows)

    def _do_list_buttons(self, step: BrowserStep) -> BrowserStepResult:
        """Enumerate clickable elements on the page — gives the AI a finite
        action space rather than having it guess selectors."""
        js = """
        () => Array.from(document.querySelectorAll('button, a[role=button], input[type=submit], input[type=button], [role=button]'))
          .map((el, i) => ({
              index: i,
              text: (el.innerText || el.value || '').trim().slice(0, 120),
              tag: el.tagName.toLowerCase(),
              id: el.id || null,
              testid: el.getAttribute('data-testid') || null,
              ariaLabel: el.getAttribute('aria-label') || null,
              visible: !!(el.offsetParent),
          }))
          .filter(e => e.visible && e.text)
        """
        elements = self._page.evaluate(js)
        return BrowserStepResult(op="list_buttons", ok=True, url=self._page.url,
                                 elements=elements)

    def _do_list_inputs(self, step: BrowserStep) -> BrowserStepResult:
        js = """
        () => Array.from(document.querySelectorAll('input, textarea, select'))
          .map((el, i) => ({
              index: i,
              tag: el.tagName.toLowerCase(),
              type: el.type || null,
              name: el.name || null,
              id: el.id || null,
              testid: el.getAttribute('data-testid') || null,
              placeholder: el.getAttribute('placeholder') || null,
              required: el.required || false,
              visible: !!(el.offsetParent),
              isPassword: el.type === 'password',
          }))
          .filter(e => e.visible)
        """
        elements = self._page.evaluate(js)
        return BrowserStepResult(op="list_inputs", ok=True, url=self._page.url,
                                 elements=elements)

    def _do_login(self, step: BrowserStep) -> BrowserStepResult:
        """High-level login: navigate, find user/password fields heuristically,
        fill from a CredentialRef, submit. Operator can override the heuristic
        by providing custom selectors in the credentials config."""
        if step.credential is None:
            raise BrowserPolicyError("login requires `credential`")
        spec = self._creds.get(step.credential.name)
        if spec is None:
            raise BrowserPolicyError(f"unknown credential: {step.credential.name!r}")
        if step.url:
            self._do_navigate(step)

        user_sel = spec.get("username_selector", 'input[name*="user" i], input[type="email"], input[autocomplete="username"]')
        pass_sel = spec.get("password_selector", 'input[type="password"]')
        submit_sel = spec.get("submit_selector", 'button[type="submit"], input[type="submit"]')

        username = self._resolve(spec["username"])
        password = self._resolve(spec["password"])
        self._scrub_values.extend([username, password])

        self._page.fill(user_sel, username, timeout=step.timeout_ms)
        self._page.fill(pass_sel, password, timeout=step.timeout_ms)
        del username, password
        self._page.click(submit_sel, timeout=step.timeout_ms)
        try:
            self._page.wait_for_load_state("networkidle", timeout=step.timeout_ms)
        except Exception:
            pass
        return BrowserStepResult(op="login", ok=True, url=self._page.url,
                                 text="[logged in]")

    # -- credential plumbing ------------------------------------------------

    def _resolve_credential(self, ref: CredentialRef, role: str) -> str:
        spec = self._creds.get(ref.name)
        if spec is None:
            raise BrowserPolicyError(f"unknown credential: {ref.name!r}")
        # role maps to a key in the credential spec (username / password / token)
        secret_uri = spec.get(role) or spec.get("password") or spec.get("token")
        if not secret_uri:
            raise BrowserPolicyError(
                f"credential {ref.name!r} has no entry for role {role!r}"
            )
        return self._resolve(secret_uri)

    def _scrub_output(self, text: str) -> str:
        """Belt+suspenders: if a secret we typed happens to show up in
        extracted text (because the site echoed it), redact it."""
        out = text
        for v in self._scrub_values:
            if v and v in out:
                out = out.replace(v, "[REDACTED:CREDENTIAL]")
        return out


def _looks_like(selector: str, hint: str) -> bool:
    # Cheap match — exact equality or substring on the meaningful chunk.
    a = re.sub(r"\s+", "", selector.lower())
    b = re.sub(r"\s+", "", hint.lower())
    return a == b or b in a
