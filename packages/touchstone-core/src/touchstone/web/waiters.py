"""Smart waiters for modern dashboards.

Single-page apps render skeletons first, then hydrate. A naive
`wait_for_selector` returns the moment the skeleton exists, not when real
data is in it. These waiters handle:

  - virtualized tables (rows render lazily as the user scrolls);
  - network-idle vs DOM-idle distinction;
  - row-count stability detection;
  - canvas-only charts (where the DOM tells you nothing).
"""

from __future__ import annotations

import time
from typing import Any


def wait_for_network_idle(page: Any, *, timeout_ms: int = 15_000, quiet_ms: int = 500) -> None:
    """Wait until the page hasn't issued a network request for `quiet_ms`.

    Playwright's built-in `wait_for_load_state('networkidle')` requires 500ms
    of silence; we wrap with an explicit timeout because some BI tools poll
    forever and never fully idle.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        # networkidle is a best-effort signal — don't fail the whole step
        # because a long-poll didn't settle.
        pass


def wait_for_stable_row_count(
    page: Any,
    selector: str,
    *,
    timeout_ms: int = 20_000,
    poll_ms: int = 300,
    stable_iterations: int = 3,
) -> int:
    """Poll the row count under `selector` until it stops growing.

    For virtualized tables, scroll the container to force lazy-load before
    each poll. Returns the final row count. Raises TimeoutError on no
    convergence.
    """
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    last_count = -1
    stable = 0
    js_count = "(sel) => document.querySelectorAll(sel + ' tr, ' + sel + ' [role=row]').length"

    while time.monotonic() < deadline:
        # Try to force lazy-load by scrolling the table container into view
        # and scrolling within it.
        page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return;
                el.scrollIntoView({block: 'end'});
                if (el.scrollHeight > el.clientHeight) {
                    el.scrollTop = el.scrollHeight;
                }
            }""",
            selector,
        )
        time.sleep(poll_ms / 1000.0)
        count = page.evaluate(js_count, selector)
        if count == last_count and count > 0:
            stable += 1
            if stable >= stable_iterations:
                return count
        else:
            stable = 0
            last_count = count
    raise TimeoutError(
        f"row count under {selector!r} did not stabilize within {timeout_ms}ms "
        f"(last seen: {last_count})"
    )


def scroll_through_virtualized(
    page: Any, container_selector: str,
    *, max_scrolls: int = 50, poll_ms: int = 250,
) -> int:
    """Scroll a virtualized container to the bottom, repeatedly, until the
    scrollHeight stops growing. Returns the number of scroll events issued.
    """
    last_height = -1
    issued = 0
    while issued < max_scrolls:
        height = page.evaluate(
            "(sel) => { const e = document.querySelector(sel); return e ? e.scrollHeight : 0; }",
            container_selector,
        )
        if height == last_height:
            return issued
        page.evaluate(
            """(sel) => {
                const e = document.querySelector(sel);
                if (e) e.scrollTop = e.scrollHeight;
            }""",
            container_selector,
        )
        time.sleep(poll_ms / 1000.0)
        last_height = height
        issued += 1
    return issued


def canvas_is_present(page: Any, selector: str = "canvas") -> bool:
    """True if the page contains a non-trivial <canvas> element. Used to warn
    that DOM extraction will miss canvas-rendered charts (Chart.js, ECharts,
    Tableau canvas mode, Grafana panels)."""
    return bool(page.evaluate(
        f"!!document.querySelector({selector!r}) && "
        f"document.querySelector({selector!r}).width > 0"
    ))
