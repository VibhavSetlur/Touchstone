"""Browser session for QA automation.

Wraps Playwright. The AI drives the browser with high-level steps. Critical
properties:

  1. **Credential-blind**: the AI passes a `CredentialRef` name, never a
     password. Literal fills on password-looking fields are refused at the
     step level.
  2. **MFA-aware**: enterprise BI tools require MFA. We solve this by
     loading an *encrypted, pre-bootstrapped storage_state* (cookies +
     localStorage) captured during an interactive `touchstone session
     bootstrap`. No MFA push happens during automation.
  3. **Origin-allowlisted**: navigation is restricted to operator-approved
     origins. Cross-origin requests are intercepted and audited.
  4. **Session-expiry-detecting**: when a page lands on a login URL or an
     MFA challenge mid-session, the browser refuses further work with a
     clear `SessionExpiredError`.
  5. **Dynamic-content-aware**: smart waiters for virtualized tables and
     lazy-loaded grids; canvas-only charts return a `canvas_detected` flag.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse


@dataclass(slots=True, frozen=True)
class CredentialRef:
    name: str


Op = Literal[
    "navigate", "click", "fill", "select", "press", "wait_for_selector",
    "wait_for_url", "wait_for_network_idle", "wait_for_stable_rows",
    "screenshot", "extract_text", "extract_table",
    "list_buttons", "list_inputs", "login",
    "bi_export", "session_status",
]


@dataclass(slots=True)
class BrowserStep:
    op: Op
    url: str | None = None
    selector: str | None = None
    value: str | None = None
    credential: CredentialRef | None = None
    timeout_ms: int = 15_000
    field_role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BrowserStepResult:
    op: Op
    ok: bool
    url: str
    text: str | None = None
    table: list[dict[str, Any]] | None = None
    screenshot_path: str | None = None
    elements: list[dict[str, Any]] | None = None
    canvas_detected: bool = False
    session_state: str = "fresh"
    error: str | None = None
    latency_ms: float = 0.0


class BrowserSessionError(Exception):
    pass


class BrowserPolicyError(BrowserSessionError):
    pass


class SessionExpiredError(BrowserSessionError):
    """The stored session is expired — operator must re-bootstrap."""


_PASSWORD_HINTS = (
    'input[type="password"]', 'input[name*="password" i]',
    'input[name*="passwd" i]', 'input[name*="secret" i]',
    'input[name*="token" i]', 'input[id*="password" i]',
    'input[autocomplete="current-password"]', 'input[autocomplete="new-password"]',
)


class BrowserSession:
    def __init__(
        self,
        *,
        allowed_origins: list[str],
        secret_resolver,
        credentials_config: dict[str, dict[str, str]],
        session_store: Any = None,
        context_dir: str | None = None,
        headless: bool = True,
        strict_expiry: bool = True,
        active_credential: str | None = None,
    ) -> None:
        self.allowed_origins = [o.rstrip("/") for o in allowed_origins]
        self._resolve = secret_resolver
        self._creds = credentials_config
        self._session_store = session_store
        self._context_dir = context_dir
        self._headless = headless
        self._strict_expiry = strict_expiry
        self._active_credential = active_credential
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._scrub_values: list[str] = []
        self._origin_log: list[str] = []
        self._session_state: str = "fresh"

    def open(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise BrowserSessionError(
                "playwright not installed. pip install 'touchstone-core[web]' "
                "&& playwright install chromium"
            ) from e
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        storage_state = self._load_storage_state()
        self._context = self._browser.new_context(
            storage_state=storage_state,
            user_agent="Mozilla/5.0 (Touchstone QA bot; +https://touchstone.dev)",
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(15_000)
        if storage_state is not None:
            self._session_state = "loaded"
        self._page.route("**/*", self._gate_request)

    def _load_storage_state(self):
        if self._active_credential and self._session_store is not None:
            try:
                stored = self._session_store.load(self._active_credential)
                return stored.storage_state
            except Exception as e:
                raise SessionExpiredError(
                    f"no usable stored session for {self._active_credential!r}: {e}. "
                    f"Run `touchstone session bootstrap {self._active_credential}`."
                ) from None
        if self._context_dir:
            from pathlib import Path
            p = Path(self._context_dir)
            if p.exists():
                return str(p)
        return None

    def _gate_request(self, route, request) -> None:
        if not self.allowed_origins:
            route.continue_()
            return
        url = request.url
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if any(origin == o or origin.startswith(o + "/") for o in self.allowed_origins):
            self._origin_log.append(origin)
            route.continue_()
        else:
            self._origin_log.append(f"BLOCKED:{origin}")
            route.abort()

    def close(self) -> None:
        for attr in ("_page", "_context", "_browser"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        if hasattr(self, "_pw"):
            self._pw.stop()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()

    def origin_log(self) -> list[str]:
        return list(self._origin_log)

    def execute(self, step: BrowserStep) -> BrowserStepResult:
        start = time.perf_counter()
        try:
            result = self._dispatch(step)
            result.latency_ms = (time.perf_counter() - start) * 1000.0
            result.session_state = self._session_state
            self._check_expiry()
            if result.text:
                result.text = self._scrub_output(result.text)
            return result
        except SessionExpiredError as e:
            self._session_state = "expired"
            return BrowserStepResult(
                op=step.op, ok=False, url=self._safe_url(),
                session_state="expired", error=f"session expired: {e}",
                latency_ms=(time.perf_counter() - start) * 1000.0,
            )
        except BrowserPolicyError as e:
            return BrowserStepResult(
                op=step.op, ok=False, url=self._safe_url(),
                session_state=self._session_state,
                error=f"policy: {e}",
                latency_ms=(time.perf_counter() - start) * 1000.0,
            )
        except Exception as e:
            return BrowserStepResult(
                op=step.op, ok=False, url=self._safe_url(),
                session_state=self._session_state,
                error=str(e),
                latency_ms=(time.perf_counter() - start) * 1000.0,
            )

    def _safe_url(self) -> str:
        try:
            return self._page.url if self._page else ""
        except Exception:
            return ""

    def _check_expiry(self) -> None:
        if not self._strict_expiry or not self._page:
            return
        from touchstone.web.session_store import (
            looks_like_login_page, looks_like_mfa_challenge,
        )
        url = self._page.url
        try:
            title = self._page.title()
        except Exception:
            title = ""
        if looks_like_login_page(url, title) or looks_like_mfa_challenge(url, title):
            self._session_state = "expired"
            raise SessionExpiredError(
                f"landed on {url!r} (title: {title!r}); session is stale. "
                f"Re-run `touchstone session bootstrap "
                f"{self._active_credential or '<credential_name>'}`."
            )

    def _dispatch(self, step: BrowserStep) -> BrowserStepResult:
        if self._session_state == "expired" and step.op != "session_status":
            raise SessionExpiredError("session is expired; re-bootstrap required")
        handlers = {
            "navigate": self._do_navigate, "click": self._do_click,
            "fill": self._do_fill, "select": self._do_select,
            "press": self._do_press,
            "wait_for_selector": self._do_wait_for_selector,
            "wait_for_url": self._do_wait_for_url,
            "wait_for_network_idle": self._do_wait_for_network_idle,
            "wait_for_stable_rows": self._do_wait_for_stable_rows,
            "screenshot": self._do_screenshot,
            "extract_text": self._do_extract_text,
            "extract_table": self._do_extract_table,
            "list_buttons": self._do_list_buttons,
            "list_inputs": self._do_list_inputs,
            "login": self._do_login,
            "bi_export": self._do_bi_export,
            "session_status": self._do_session_status,
        }
        h = handlers.get(step.op)
        if h is None:
            raise BrowserSessionError(f"unknown op: {step.op}")
        return h(step)

    def _check_origin(self, url: str) -> None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if not any(origin == o or origin.startswith(o + "/") for o in self.allowed_origins):
            raise BrowserPolicyError(
                f"origin not in allowed_origins: {origin}. "
                "Add it to web.allowed_origins in touchstone.toml."
            )

    def _check_not_credential_field(self, selector: str | None) -> None:
        if not selector:
            return
        if any(_looks_like(selector, hint) for hint in _PASSWORD_HINTS):
            raise BrowserPolicyError(
                f"refused to fill credential-looking field {selector!r} with "
                "a literal value. Pass a `credential` reference instead."
            )

    # -- ops --------------------------------------------------------------

    def _do_navigate(self, step):
        if not step.url:
            raise BrowserSessionError("navigate requires url")
        self._check_origin(step.url)
        self._page.goto(step.url, timeout=step.timeout_ms, wait_until="domcontentloaded")
        return BrowserStepResult(op="navigate", ok=True, url=self._page.url)

    def _do_click(self, step):
        if not step.selector:
            raise BrowserSessionError("click requires selector")
        self._page.click(step.selector, timeout=step.timeout_ms)
        return BrowserStepResult(op="click", ok=True, url=self._page.url)

    def _do_fill(self, step):
        if not step.selector:
            raise BrowserSessionError("fill requires selector")
        if step.credential is not None:
            value = self._resolve_credential(step.credential, step.field_role or "value")
            self._scrub_values.append(value)
            self._page.fill(step.selector, value, timeout=step.timeout_ms)
            del value
            return BrowserStepResult(op="fill", ok=True, url=self._page.url,
                                     text="[credential filled]")
        self._check_not_credential_field(step.selector)
        if step.value is None:
            raise BrowserSessionError("fill requires value or credential")
        self._page.fill(step.selector, step.value, timeout=step.timeout_ms)
        return BrowserStepResult(op="fill", ok=True, url=self._page.url)

    def _do_select(self, step):
        if not step.selector or step.value is None:
            raise BrowserSessionError("select requires selector and value")
        self._page.select_option(step.selector, step.value, timeout=step.timeout_ms)
        return BrowserStepResult(op="select", ok=True, url=self._page.url)

    def _do_press(self, step):
        if step.value is None:
            raise BrowserSessionError("press requires value (key name)")
        if step.selector:
            self._page.press(step.selector, step.value, timeout=step.timeout_ms)
        else:
            self._page.keyboard.press(step.value)
        return BrowserStepResult(op="press", ok=True, url=self._page.url)

    def _do_wait_for_selector(self, step):
        if not step.selector:
            raise BrowserSessionError("wait_for_selector requires selector")
        self._page.wait_for_selector(step.selector, timeout=step.timeout_ms)
        return BrowserStepResult(op="wait_for_selector", ok=True, url=self._page.url)

    def _do_wait_for_url(self, step):
        if not step.url:
            raise BrowserSessionError("wait_for_url requires url")
        self._page.wait_for_url(step.url, timeout=step.timeout_ms)
        return BrowserStepResult(op="wait_for_url", ok=True, url=self._page.url)

    def _do_wait_for_network_idle(self, step):
        from touchstone.web.waiters import wait_for_network_idle
        wait_for_network_idle(self._page, timeout_ms=step.timeout_ms)
        return BrowserStepResult(op="wait_for_network_idle", ok=True, url=self._page.url)

    def _do_wait_for_stable_rows(self, step):
        from touchstone.web.waiters import wait_for_stable_row_count
        if not step.selector:
            raise BrowserSessionError("wait_for_stable_rows requires selector")
        count = wait_for_stable_row_count(self._page, step.selector, timeout_ms=step.timeout_ms)
        return BrowserStepResult(op="wait_for_stable_rows", ok=True,
                                 url=self._page.url, text=f"stabilized at {count} rows")

    def _do_screenshot(self, step):
        import tempfile
        path = step.metadata.get("path") or tempfile.mktemp(suffix=".png")
        self._page.screenshot(path=path, full_page=bool(step.metadata.get("full_page")))
        return BrowserStepResult(op="screenshot", ok=True, url=self._page.url,
                                 screenshot_path=path)

    def _do_extract_text(self, step):
        if step.selector:
            text = self._page.locator(step.selector).inner_text(timeout=step.timeout_ms)
        else:
            text = self._page.content()
        return BrowserStepResult(op="extract_text", ok=True, url=self._page.url, text=text)

    def _do_extract_table(self, step):
        from touchstone.web.extractor import extract_table as _extract
        from touchstone.web.waiters import canvas_is_present, wait_for_stable_row_count
        sel = step.selector or "table"
        try:
            wait_for_stable_row_count(self._page, sel,
                                       timeout_ms=min(step.timeout_ms, 20_000),
                                       stable_iterations=2)
        except TimeoutError:
            pass
        rows = _extract(self._page, sel, timeout_ms=step.timeout_ms)
        canvas = canvas_is_present(self._page)
        return BrowserStepResult(op="extract_table", ok=True, url=self._page.url,
                                 table=rows, canvas_detected=canvas)

    def _do_list_buttons(self, step):
        js = """
        () => Array.from(document.querySelectorAll(
            'button, a[role=button], input[type=submit], input[type=button], [role=button]'
        )).map((el, i) => ({
            index: i,
            text: (el.innerText || el.value || '').trim().slice(0, 120),
            tag: el.tagName.toLowerCase(),
            id: el.id || null,
            testid: el.getAttribute('data-testid') || null,
            ariaLabel: el.getAttribute('aria-label') || null,
            visible: !!(el.offsetParent),
        })).filter(e => e.visible && e.text)
        """
        elements = self._page.evaluate(js)
        return BrowserStepResult(op="list_buttons", ok=True, url=self._page.url,
                                 elements=elements)

    def _do_list_inputs(self, step):
        js = """
        () => Array.from(document.querySelectorAll('input, textarea, select'))
          .map((el, i) => ({
              index: i, tag: el.tagName.toLowerCase(),
              type: el.type || null, name: el.name || null, id: el.id || null,
              testid: el.getAttribute('data-testid') || null,
              placeholder: el.getAttribute('placeholder') || null,
              required: el.required || false,
              visible: !!(el.offsetParent),
              isPassword: el.type === 'password',
          })).filter(e => e.visible)
        """
        elements = self._page.evaluate(js)
        return BrowserStepResult(op="list_inputs", ok=True, url=self._page.url,
                                 elements=elements)

    def _do_login(self, step):
        if self._session_state == "loaded":
            if step.url:
                self._do_navigate(step)
            return BrowserStepResult(op="login", ok=True, url=self._page.url,
                                     text="[stored session in use]")
        if step.credential is None:
            raise BrowserPolicyError("login requires `credential`")
        spec = self._creds.get(step.credential.name)
        if spec is None:
            raise BrowserPolicyError(f"unknown credential: {step.credential.name!r}")
        if step.url:
            self._do_navigate(step)
        user_sel = spec.get("username_selector",
                            'input[name*="user" i], input[type="email"], input[autocomplete="username"]')
        pass_sel = spec.get("password_selector", 'input[type="password"]')
        submit_sel = spec.get("submit_selector",
                              'button[type="submit"], input[type="submit"]')
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
        from touchstone.web.session_store import looks_like_mfa_challenge
        try:
            title = self._page.title()
        except Exception:
            title = ""
        if looks_like_mfa_challenge(self._page.url, title):
            raise SessionExpiredError(
                "login form submitted but landed on an MFA challenge — headless "
                f"automation cannot complete MFA. Run `touchstone session bootstrap "
                f"{step.credential.name}` once."
            )
        return BrowserStepResult(op="login", ok=True, url=self._page.url,
                                 text="[logged in]")

    def _do_bi_export(self, step):
        from touchstone.web.exporters import EXPORTERS, export_via_browser
        meta = step.metadata or {}
        exporter_name = meta.get("exporter")
        args = meta.get("args") or {}
        if exporter_name not in EXPORTERS:
            raise BrowserSessionError(f"unknown exporter: {exporter_name!r}")
        export_url = EXPORTERS[exporter_name](**args)
        self._check_origin(export_url)
        rows = export_via_browser(self._page, export_url,
                                  format=meta.get("format", "csv"),
                                  timeout_ms=step.timeout_ms)
        return BrowserStepResult(op="bi_export", ok=True, url=self._page.url,
                                 table=rows)

    def _do_session_status(self, step):
        return BrowserStepResult(
            op="session_status", ok=True,
            url=self._page.url if self._page else "",
            text=self._session_state, session_state=self._session_state,
        )

    def _resolve_credential(self, ref: CredentialRef, role: str) -> str:
        spec = self._creds.get(ref.name)
        if spec is None:
            raise BrowserPolicyError(f"unknown credential: {ref.name!r}")
        secret_uri = spec.get(role) or spec.get("password") or spec.get("token")
        if not secret_uri:
            raise BrowserPolicyError(f"credential {ref.name!r} has no entry for role {role!r}")
        return self._resolve(secret_uri)

    def _scrub_output(self, text: str) -> str:
        out = text
        for v in self._scrub_values:
            if v and v in out:
                out = out.replace(v, "[REDACTED:CREDENTIAL]")
        return out


def _looks_like(selector: str, hint: str) -> bool:
    a = re.sub(r"\s+", "", selector.lower())
    b = re.sub(r"\s+", "", hint.lower())
    return a == b or b in a
