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
#
# Each helper returns the URL appropriate for the BI tool's CSV / JSON
# export. Where a URL pattern varies by version or deployment, we use the
# most-common current-supported-major form and document the variants so
# operators can override via the lower-level `export_via_browser`.
#
# Coverage notes (verified against vendor docs as of 2026-06):
#   Looker     — `look/{id}.csv` is the stable per-look export; for tile
#                exports from a dashboard, prefer the look URL when possible.
#   Tableau    — `<view_url>.csv` works on Cloud / Server 2019.4+.
#   Grafana    — render plugin must be installed for /render/d-csv/.
#                Alternative: data-source proxy + manual JSON→CSV.
#   Metabase   — /api/card/<id>/query/csv since v0.41.
#   Superset   — /api/v1/chart/<id>/data/?format=csv since v1.5.


def looker_dashboard_csv_url(dashboard_url: str, element_id: int | str) -> str:
    """Looker per-look CSV: `/looks/{id}.csv`.

    This is the most stable Looker export endpoint — works on Looker Original
    and current Looker by Google Cloud. For tiles that aren't look-backed
    (raw query tiles), the path differs and operators should use the
    Looker REST API `look.run_look_inline_query` directly (planned
    `looker_api_query` exporter).
    """
    p = urlparse(dashboard_url)
    return f"{p.scheme}://{p.netloc}/looks/{element_id}.csv"


def tableau_view_csv_url(view_url: str) -> str:
    """Tableau Cloud / Server view CSV: append `.csv` to the view URL.

    Works on Tableau Cloud and Tableau Server 2019.4+. Some auth proxies
    require `:embed=y` in the URL before `.csv` is honored.
    Workbook-level CSV requires the Tableau REST API (planned).
    """
    base = view_url.split("?", 1)[0].rstrip("/")
    qs = view_url.split("?", 1)[1] if "?" in view_url else ""
    return f"{base}.csv" + (f"?{qs}" if qs else "")


def grafana_panel_csv_url(grafana_base: str, dashboard_uid: str,
                          panel_id: int, *, from_: str = "now-7d", to: str = "now") -> str:
    """Grafana per-panel CSV via the render endpoint.

    Requires Grafana Image Renderer plugin to be installed. If not
    installed, the request returns HTML; fall back to a direct
    data-source query via `/api/ds/query`.
    """
    base = grafana_base.rstrip("/")
    return (
        f"{base}/render/d-csv/{dashboard_uid}/"
        f"?panelId={panel_id}&from={from_}&to={to}"
    )


def metabase_card_csv_url(metabase_base: str, card_id: int) -> str:
    """Metabase per-card CSV. Stable since v0.41."""
    return f"{metabase_base.rstrip('/')}/api/card/{card_id}/query/csv"


def superset_chart_csv_url(superset_base: str, slice_id: int) -> str:
    """Apache Superset chart CSV. Works on Superset 1.5+."""
    return f"{superset_base.rstrip('/')}/api/v1/chart/{slice_id}/data/?format=csv"


# Registry the AI / playbooks can call by name.
EXPORTERS = {
    "looker_tile_csv": looker_dashboard_csv_url,
    "tableau_view_csv": tableau_view_csv_url,
    "grafana_panel_csv": grafana_panel_csv_url,
    "metabase_card_csv": metabase_card_csv_url,
    "superset_chart_csv": superset_chart_csv_url,
}
