"""BI-native data exporters.

The most reliable way to read a Looker / Tableau / Grafana / Metabase
dashboard is NOT to scrape the rendered DOM — it's to use the same
authenticated session to hit the tool's CSV / JSON export endpoint, which
returns the ground-truth data the chart was rendered from.

These helpers wrap the export endpoints. Authentication piggybacks on the
existing Playwright context (cookies from the bootstrap session), so the AI
never touches credentials and we never have to maintain a second auth flow.

Each exporter returns a list[dict] in the same shape as
`extractor.extract_table()`, so the verifier code works against either source
transparently.
"""

from __future__ import annotations

import csv
import io
from typing import Any
from urllib.parse import urlparse


def export_via_browser(page: Any, export_url: str, *, format: str = "csv",
                       timeout_ms: int = 30_000) -> list[dict[str, Any]]:
    """Issue an authenticated GET via the browser's cookies, parse CSV/JSON.

    Uses `page.evaluate(fetch(...))` so cookies are sent automatically with
    the request. Avoids spawning a second HTTP client with its own auth
    state.
    """
    js = """
    async (args) => {
      const r = await fetch(args.url, {credentials: 'include', headers: {Accept: 'text/csv, application/json'}});
      if (!r.ok) throw new Error(`fetch failed: ${r.status} ${r.statusText}`);
      const text = await r.text();
      return {status: r.status, contentType: r.headers.get('Content-Type') || '', body: text};
    }
    """
    resp = page.evaluate(js, {"url": export_url})
    body = resp["body"]
    if format == "json" or "json" in resp["contentType"]:
        import json as _json
        data = _json.loads(body)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return [data]
    # default to CSV
    reader = csv.DictReader(io.StringIO(body))
    return list(reader)


# -- Per-tool helpers -----------------------------------------------------

def looker_dashboard_csv_url(dashboard_url: str, element_id: int | str) -> str:
    """Looker's per-tile CSV export. The URL pattern is stable across Looker
    versions; the dashboard URL gives us the host."""
    p = urlparse(dashboard_url)
    return f"{p.scheme}://{p.netloc}/dashboards/elements/{element_id}/csv"


def tableau_view_csv_url(view_url: str) -> str:
    """Tableau Server / Tableau Cloud `?format=csv` data export.

    Some Tableau deployments require the view URL to end in `:embed=y`
    before `?format=csv` is honored; we tolerate both shapes.
    """
    if "?" in view_url:
        sep = "&"
    else:
        sep = "?"
    return f"{view_url}{sep}:format=csv"


def grafana_panel_csv_url(grafana_base: str, dashboard_uid: str,
                          panel_id: int, *, from_: str = "now-7d", to: str = "now") -> str:
    """Grafana 10+ supports CSV export via the API panel render endpoint."""
    return (
        f"{grafana_base.rstrip('/')}/api/datasources/proxy/uid/{dashboard_uid}"
        f"/query?panelId={panel_id}&from={from_}&to={to}&format=csv"
    )


def metabase_card_csv_url(metabase_base: str, card_id: int) -> str:
    """Metabase has a clean /api/card/<id>/query/csv endpoint."""
    return f"{metabase_base.rstrip('/')}/api/card/{card_id}/query/csv"


# Registry the AI / playbooks can call by name.
EXPORTERS = {
    "looker_tile_csv": looker_dashboard_csv_url,
    "tableau_view_csv": tableau_view_csv_url,
    "grafana_panel_csv": grafana_panel_csv_url,
    "metabase_card_csv": metabase_card_csv_url,
}
