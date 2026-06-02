"""Pre-release sign-off playbook.

Run before tagging a release. Walks through a bundle of expectations files
across all production tables, summarizes pass/fail, and emits a checklist
the release engineer can sign off on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from touchstone.playbooks.base import Playbook, PlaybookReport, PlaybookStep
from touchstone.qa.validator import check_data_quality
from touchstone.security.gateway import Gateway


@dataclass(slots=True)
class PreReleaseCheck(Playbook):
    name: str = "pre_release_check"
    gateway: Gateway | None = None

    def run(  # type: ignore[override]
        self,
        *,
        assistant_id: str,
        assistant_session: str,
        connection: str,
        expectations_dir: str,
    ) -> PlaybookReport:
        report = PlaybookReport(playbook=self.name)
        directory = Path(expectations_dir).expanduser()
        if not directory.is_dir():
            report.steps.append(PlaybookStep(
                name="locate_expectations", ok=False,
                detail=f"not a directory: {directory}",
            ))
            return report

        files = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
        if not files:
            report.steps.append(PlaybookStep(
                name="locate_expectations", ok=False,
                detail=f"no .yaml/.yml files in {directory}",
            ))
            return report

        passed = 0
        failed_tables: list[str] = []
        for f in files:
            try:
                v = check_data_quality(
                    self.gateway,
                    assistant_id=assistant_id, assistant_session=assistant_session,
                    connection=connection, expectations_path=f,
                )
                ok = v.passed
                report.steps.append(PlaybookStep(
                    name=f"check:{f.stem}", ok=ok,
                    detail=f"{sum(1 for r in v.results if r.passed)}/{len(v.results)} passed",
                    data={"results": [{"name": r.name, "passed": r.passed,
                                       "observed": r.observed, "expected": r.expected}
                                      for r in v.results]},
                ))
                if ok:
                    passed += 1
                else:
                    failed_tables.append(v.table)
            except Exception as e:
                report.steps.append(PlaybookStep(
                    name=f"check:{f.stem}", ok=False, detail=str(e),
                ))
                failed_tables.append(f.stem)

        report.summary = (
            f"Pre-release: {passed}/{len(files)} expectations files passed. "
            f"{len(failed_tables)} table(s) need attention."
        )
        if failed_tables:
            report.suggested_actions.append(
                f"DO NOT release until these resolve: {', '.join(failed_tables)}"
            )
        return report
