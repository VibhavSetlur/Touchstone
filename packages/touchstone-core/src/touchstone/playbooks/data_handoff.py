"""Data-handoff playbook.

A new analyst inherits a table. Generate a one-page brief: schema, owners,
recent changes, known quirks (from knowledge notes), top values for category
columns, and a `gotchas` checklist of things to watch for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from touchstone.github_intel.activity import recent_changes_to
from touchstone.github_intel.owners import find_owners
from touchstone.knowledge.store import KnowledgeStore
from touchstone.playbooks.base import Playbook, PlaybookReport, PlaybookStep
from touchstone.qa.profiler import profile_table
from touchstone.types import TableRef


@dataclass(slots=True)
class DataHandoff(Playbook):
    name: str = "data_handoff"
    gateway: Any = None
    knowledge: KnowledgeStore | None = None
    github_api: Any = None

    def run(  # type: ignore[override]
        self,
        *,
        assistant_id: str,
        assistant_session: str,
        connection: str,
        table: str,
        schema: str | None = None,
        repo: str | None = None,
        table_path_in_repo: str | None = None,
    ) -> PlaybookReport:
        report = PlaybookReport(playbook=self.name)

        # Profile
        prof = profile_table(
            self.gateway,
            assistant_id=assistant_id, assistant_session=assistant_session,
            connection=connection,
            table=TableRef(name=table, schema=schema),
        )
        report.steps.append(PlaybookStep(
            name="profile", ok=True,
            detail=f"{prof.row_count:,} rows, {len(prof.columns)} columns",
            data={
                "row_count": prof.row_count,
                "columns": [{"name": c.name, "type": c.type,
                             "semantic_type": c.semantic_type,
                             "null_rate": c.null_rate,
                             "top_values": c.top_values[:3]} for c in prof.columns],
                "pii_summary": prof.pii_summary,
            },
        ))

        # Owners
        if table_path_in_repo:
            suggestions = find_owners(
                store=self.knowledge, api=self.github_api,
                repo=repo, path=table_path_in_repo,
            )
            owners = [s.handle for s in suggestions[:5]]
            report.steps.append(PlaybookStep(
                name="owners", ok=True, detail=", ".join(owners) or "no owners found",
                data={"owners": owners},
            ))

        # Recent changes
        if self.github_api and repo and table_path_in_repo:
            changes = recent_changes_to(self.github_api, repo, table_path_in_repo, days=60)
            report.steps.append(PlaybookStep(
                name="recent_changes", ok=True,
                detail=f"{len(changes)} commit(s) in last 60 days",
                data={"changes": [c.__dict__ for c in changes[:10]]},
            ))

        # Known quirks
        if self.knowledge:
            notes = self.knowledge.notes_for(f"table:{table}")
            if notes:
                report.steps.append(PlaybookStep(
                    name="known_quirks", ok=True,
                    detail=f"{len(notes)} known note(s)",
                    data={"notes": [{"body": n.body, "tags": n.tags} for n in notes]},
                ))

        # Gotchas heuristic from profile
        gotchas: list[str] = []
        for col in prof.columns:
            if col.null_rate is not None and col.null_rate > 0.5:
                gotchas.append(f"{col.name} is mostly NULL ({col.null_rate:.1%}) — confirm it's used.")
            if col.semantic_type == "timestamp" and col.null_rate == 0:
                gotchas.append(f"{col.name} is a never-null timestamp — confirm it's set by the writer, not a default.")
        if prof.pii_summary:
            gotchas.append("PII present — verify your queries respect Touchstone's masking before sharing results.")
        for g in gotchas:
            report.suggested_actions.append(g)

        report.summary = (
            f"Handoff brief for {table}: {prof.row_count:,} rows, "
            f"{len(gotchas)} gotcha(s) to be aware of."
        )
        return report
