"""Extract structured data from rendered web pages.

`extract_table` reads an HTML <table> into a list of dicts. `extract_text`
returns the inner text of a selector. Both are used by the verifier to
compare what the user sees on a dashboard to what the DB actually contains.
"""

from __future__ import annotations

from typing import Any


def extract_text(page: Any, selector: str, *, timeout_ms: int = 10_000) -> str:
    return page.locator(selector).inner_text(timeout=timeout_ms)


def extract_table(page: Any, selector: str = "table", *, timeout_ms: int = 10_000) -> list[dict[str, Any]]:
    """Parse the first matching HTML <table> into rows.

    Handles <thead>/<tbody>, colspans (best-effort), and stripped whitespace.
    For React/Vue virtualized tables that don't render as <table>, the
    caller should pass a more specific selector and use the JS-evaluation
    fallback below.
    """
    page.wait_for_selector(selector, timeout=timeout_ms)
    js = r"""
    (sel) => {
      const t = document.querySelector(sel);
      if (!t) return [];
      // Real <table>: parse <thead>/<tbody> properly.
      if (t.tagName === 'TABLE') {
        const headerCells = t.querySelectorAll('thead th, thead td');
        const headers = headerCells.length
          ? Array.from(headerCells).map(h => h.innerText.trim())
          : Array.from(t.querySelectorAll('tr:first-child td, tr:first-child th'))
              .map(h => h.innerText.trim());
        const rows = Array.from(t.querySelectorAll('tbody tr, tr')).slice(
          headerCells.length ? 0 : 1
        );
        return rows.map(r => {
          const cells = Array.from(r.querySelectorAll('td, th')).map(c => c.innerText.trim());
          const obj = {};
          headers.forEach((h, i) => obj[h || `col_${i}`] = cells[i] ?? '');
          return obj;
        });
      }
      // Generic grid (role=table / role=grid).
      const headerEls = t.querySelectorAll('[role="columnheader"]');
      const rowEls = t.querySelectorAll('[role="row"]');
      const headers = Array.from(headerEls).map(h => h.innerText.trim());
      return Array.from(rowEls).map(r => {
        const cells = Array.from(r.querySelectorAll('[role="cell"], [role="gridcell"]'))
          .map(c => c.innerText.trim());
        const obj = {};
        headers.forEach((h, i) => obj[h || `col_${i}`] = cells[i] ?? '');
        return obj;
      }).filter(o => Object.keys(o).length);
    }
    """
    return page.evaluate(js, selector)
