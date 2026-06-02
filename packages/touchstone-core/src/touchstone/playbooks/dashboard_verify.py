"""Dashboard-verify playbook.

Compares what a rendered dashboard shows to what the underlying DB query
returns. Catches the "$10,500.56 in the warehouse → $10.5K in the chart"
class of bug.

Inputs:
  - dashboard_url       — the dashboard the operator wants to verify
  - credential          — a CredentialRef name (NOT a raw credential)
  - table_selector      — CSS selector for the rendered table on the page
  - connection          — the DB to compare against
  - sql                 — the "ground truth" query
  - key_column / value_columns — alignment spec
  - tolerance           — float; 0.0 means exact, 0.001 means within 0.1%
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from touchstone.playbooks.base import Playbook, PlaybookReport, PlaybookStep
from touchstone.web.browser import BrowserSession, BrowserStep, CredentialRef
from touchstone.web.verifier import verify_rendered_against_db


@dataclass(slots=True)
class DashboardVerify(Playbook):
    name: str = "dashboard_verify"
    gateway: Any = None
    browser_factory: Any = None   # callable() -> BrowserSession

    def run(  # type: ignore[override]
        self,
        *,
        assistant_id: str,
        assistant_session: str,
        dashboard_url: str,
        credential: str | None,
        table_selector: str,
        connection: str,
        sql: str,
        key_column: str,
        value_columns: list[str],
        tolerance: float = 0.0,
        login_url: str | None = None,
    ) -> PlaybookReport:
        report = PlaybookReport(playbook=self.name)

        if self.browser_factory is None:
            report.steps.append(PlaybookStep(
                name="open_browser", ok=False,
                detail="no browser factory wired up — install Playwright and configure web.allowed_origins",
            ))
            return report

        with self.browser_factory() as browser:  # type: BrowserSession
            if credential:
                login = browser.execute(BrowserStep(
                    op="login", url=login_url or dashboard_url,
                    credential=CredentialRef(credential),
                ))
                report.steps.append(PlaybookStep(
                    name="login", ok=login.ok, detail=login.error or "ok",
                ))
                if not login.ok:
                    return report

            nav = browser.execute(BrowserStep(op="navigate", url=dashboard_url))
            report.steps.append(PlaybookStep(
                name="navigate", ok=nav.ok, detail=nav.error or nav.url,
            ))
            if not nav.ok:
                return report

            verify_report = verify_rendered_against_db(
                self.gateway,
                assistant_id=assistant_id, assistant_session=assistant_session,
                browser=browser, table_selector=table_selector,
                connection=connection, sql=sql,
                key_column=key_column, value_columns=value_columns,
                tolerance=tolerance,
            )
            disc = verify_report.cell_discrepancies
            report.steps.append(PlaybookStep(
                name="compare", ok=(not disc),
                detail=(
                    f"rendered={verify_report.rendered_rows} db={verify_report.db_rows} "
                    f"mismatches={len(disc)}"
                ),
                data={
                    "discrepancies": [d.__dict__ for d in disc[:50]],
                    "rows_only_rendered": verify_report.rows_only_rendered,
                    "rows_only_db": verify_report.rows_only_db,
                },
            ))
            for d in disc[:10]:
                report.suggested_actions.append(
                    f"investigate {d.column} for key={d.key}: rendered={d.rendered} db={d.db} "
                    f"({d.severity})"
                )

        report.summary = (
            f"Dashboard verification: "
            f"{'pass' if report.passed else f'{len(disc)} discrepancy(ies)'}"
        )
        return report
