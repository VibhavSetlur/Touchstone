"""Migration-safety playbook.

Inputs: connection (sandbox), table, file changes from a PR.
Steps:
  1. Profile the table on the sandbox (which has the migration applied).
  2. Run the PR-impact analyzer on the file changes.
  3. Look up owners for the touched paths (knowledge store + GitHub blame).
  4. Suggest expectations to add.
  5. Draft a stakeholder note for the listed owners.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from touchstone.github_intel.owners import find_owners
from touchstone.knowledge.store import KnowledgeStore
from touchstone.playbooks.base import Playbook, PlaybookReport, PlaybookStep
from touchstone.qa.pr import FileChange, analyze_pr_data_impact
from touchstone.qa.profiler import profile_table
from touchstone.qa.test_gen import generate_test_cases
from touchstone.types import TableRef


@dataclass(slots=True)
class MigrationSafety(Playbook):
    name: str = "migration_safety"
    gateway: Any = None
    knowledge: KnowledgeStore | None = None
    github_api: Any = None

    def run(  # type: ignore[override]
        self,
        *,
        assistant_id: str,
        assistant_session: str,
        sandbox_connection: str,
        table_name: str,
        schema: str | None = None,
        repo: str | None = None,
        changes: list[FileChange] | None = None,
        dialect: str = "",
    ) -> PlaybookReport:
        report = PlaybookReport(playbook=self.name)

        # 1. Profile
        try:
            prof = profile_table(
                self.gateway,
                assistant_id=assistant_id, assistant_session=assistant_session,
                connection=sandbox_connection,
                table=TableRef(name=table_name, schema=schema),
            )
            report.steps.append(PlaybookStep(
                name="profile_sandbox", ok=True,
                detail=f"{prof.row_count:,} rows, {len(prof.columns)} columns",
                data={"row_count": prof.row_count, "pii_summary": prof.pii_summary},
            ))
        except Exception as e:
            report.steps.append(PlaybookStep(
                name="profile_sandbox", ok=False, detail=str(e),
            ))
            return report

        # 2. PR-impact
        if changes:
            pr_report = analyze_pr_data_impact(changes=changes, dialect=dialect)
            report.steps.append(PlaybookStep(
                name="pr_impact_analysis", ok=True,
                detail=(f"{len(pr_report.tables)} table changes · "
                        f"{len(pr_report.columns)} column changes · "
                        f"{len(pr_report.downstream_risks)} risks"),
                data={"tables": [t.__dict__ for t in pr_report.tables],
                      "columns": [c.__dict__ for c in pr_report.columns]},
            ))
            for s in pr_report.suggested_tests:
                report.suggested_actions.append(f"add test: {s}")
        else:
            report.steps.append(PlaybookStep(
                name="pr_impact_analysis", ok=True,
                detail="no SQL file changes supplied — skipped",
            ))

        # 3. Owners
        if repo and changes:
            owner_set: dict[str, list[str]] = {}
            for ch in changes:
                suggestions = find_owners(
                    store=self.knowledge, api=self.github_api,
                    repo=repo, path=ch.path,
                )
                owner_set[ch.path] = [s.handle for s in suggestions[:3]]
            unique_owners = sorted({o for v in owner_set.values() for o in v if o})
            report.steps.append(PlaybookStep(
                name="identify_owners", ok=True,
                detail=f"{len(unique_owners)} owner(s): {', '.join(unique_owners)}"
                       if unique_owners else "no owners found",
                data={"by_path": owner_set},
            ))
            for o in unique_owners:
                report.suggested_actions.append(f"ping @{o} for migration review")

        # 4. Test-suggestions from profile
        gen = generate_test_cases(prof)
        if gen.expectations:
            for exp in gen.expectations:
                report.suggested_actions.append(f"expectation: {exp}")
            report.steps.append(PlaybookStep(
                name="suggest_expectations", ok=True,
                detail=f"{len(gen.expectations)} expectation(s) drafted",
                data={"yaml": gen.to_yaml()},
            ))

        # 5. Tasks
        report.next_tasks.append({
            "title": f"Add data-quality expectations for {table_name} from migration_safety run",
            "owner": None, "related": [f"table:{table_name}"],
        })

        report.summary = (
            f"Migration safety check for {table_name}: "
            f"{'pass' if report.passed else 'attention required'}. "
            f"{len(report.suggested_actions)} suggested action(s)."
        )
        return report
