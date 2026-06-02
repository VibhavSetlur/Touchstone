"""Anomaly-triage playbook.

"This metric looks off in <table.column>. Triage it."

Steps:
  1. Pull the last N values + their delta from prior baseline.
  2. Find recent PRs that touched the model/table.
  3. Identify owners.
  4. Cross-check with knowledge-store notes.
  5. Draft a stakeholder message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from touchstone.github_intel.activity import recent_changes_to
from touchstone.github_intel.owners import find_owners
from touchstone.knowledge.store import KnowledgeStore
from touchstone.playbooks.base import Playbook, PlaybookReport, PlaybookStep
from touchstone.security.gateway import Gateway, ToolCallContext


@dataclass(slots=True)
class AnomalyTriage(Playbook):
    name: str = "anomaly_triage"
    gateway: Gateway | None = None
    knowledge: KnowledgeStore | None = None
    github_api: Any = None

    def run(  # type: ignore[override]
        self,
        *,
        assistant_id: str,
        assistant_session: str,
        connection: str,
        metric_sql: str,
        metric_label: str,
        table_path_in_repo: str | None = None,
        repo: str | None = None,
    ) -> PlaybookReport:
        report = PlaybookReport(playbook=self.name)

        # 1. Recent values
        out = self.gateway.execute(ToolCallContext(
            assistant_id=assistant_id, assistant_session=assistant_session,
            tool="anomaly_triage", connection=connection, sql=metric_sql,
        ))
        rows = out.masked.result.to_dicts()
        report.steps.append(PlaybookStep(
            name="recent_metric_values", ok=True,
            detail=f"{len(rows)} datapoint(s)",
            data={"rows": rows[:50]},
        ))

        # 2. Recent commits touching the table
        commits = []
        if self.github_api and repo and table_path_in_repo:
            commits = recent_changes_to(self.github_api, repo, table_path_in_repo, days=30)
            report.steps.append(PlaybookStep(
                name="recent_changes", ok=True,
                detail=f"{len(commits)} commit(s) in the last 30 days",
                data={"commits": [c.__dict__ for c in commits[:20]]},
            ))

        # 3. Owners
        owners = []
        if table_path_in_repo:
            suggestions = find_owners(
                store=self.knowledge, api=self.github_api,
                repo=repo, path=table_path_in_repo,
            )
            owners = [s.handle for s in suggestions[:3]]
            report.steps.append(PlaybookStep(
                name="identify_owners", ok=True,
                detail=", ".join(owners) if owners else "no owners found",
                data={"owners": owners},
            ))

        # 4. Knowledge-store notes
        if self.knowledge:
            notes = self.knowledge.notes_for(f"table:{metric_label}")
            if notes:
                report.steps.append(PlaybookStep(
                    name="known_quirks", ok=True,
                    detail=f"{len(notes)} note(s) on file",
                    data={"notes": [{"body": n.body, "author": n.author,
                                     "created_at": n.created_at} for n in notes]},
                ))

        # 5. Stakeholder draft
        recent_authors = [c.author for c in commits if c.author][:3]
        ping_list = sorted(set(owners + recent_authors))
        draft = (
            f"Heads up on {metric_label}: recent values look unusual. "
            f"Last {len(rows)} datapoints attached. "
            f"Touched recently by: {', '.join(c.sha for c in commits[:3]) or 'no recent commits'}. "
            f"Suggested to look at: {', '.join('@' + p for p in ping_list) or 'no obvious owner'}."
        )
        report.suggested_actions.append(f"draft message: {draft}")

        report.next_tasks.append({
            "title": f"Investigate anomaly in {metric_label}",
            "owner": ping_list[0] if ping_list else None,
            "related": [f"table:{metric_label}"],
        })

        report.summary = (
            f"Anomaly triage for {metric_label}: pulled metric, "
            f"{len(commits)} recent change(s), {len(owners)} owner(s)."
        )
        return report
