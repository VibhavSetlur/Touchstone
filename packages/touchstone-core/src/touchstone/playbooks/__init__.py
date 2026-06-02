"""Playbooks — pre-canned QA workflows.

A playbook is a Python class with a `run()` method that orchestrates several
gateway calls + knowledge lookups + (optionally) browser steps and emits a
structured report.

Why playbooks: AI assistants are good at deciding what to do step-by-step,
but for routine work it's better to encode the routine — both for
reproducibility ("this is how we sign off a migration") and for cost ("don't
spend 20 tool calls reasoning through the same dance every week").

Bundled playbooks:
  - migration_safety    — for a PR that changes schema: profile before/after,
                          check downstream refs, suggest expectations.
  - dashboard_verify    — for a dashboard URL + SQL: browse, extract, diff
                          against DB, flag rendering bugs.
  - anomaly_triage      — for "this metric looks off": pull recent values,
                          who touched it, related PRs, draft a stakeholder
                          note.
  - pre_release_check   — run a bundle of validations + diffs before a
                          release tag is cut.
  - data_handoff        — generate a one-page summary for a new analyst
                          inheriting a table (schema, owners, recent changes,
                          known quirks).

Operators add their own — see docs/playbooks/writing-a-playbook.md.
"""

from touchstone.playbooks.base import Playbook, PlaybookReport
from touchstone.playbooks.migration_safety import MigrationSafety
from touchstone.playbooks.dashboard_verify import DashboardVerify
from touchstone.playbooks.anomaly_triage import AnomalyTriage
from touchstone.playbooks.pre_release_check import PreReleaseCheck
from touchstone.playbooks.data_handoff import DataHandoff

REGISTRY: dict[str, type[Playbook]] = {
    "migration_safety": MigrationSafety,
    "dashboard_verify": DashboardVerify,
    "anomaly_triage": AnomalyTriage,
    "pre_release_check": PreReleaseCheck,
    "data_handoff": DataHandoff,
}

__all__ = [
    "Playbook", "PlaybookReport", "REGISTRY",
    "MigrationSafety", "DashboardVerify", "AnomalyTriage",
    "PreReleaseCheck", "DataHandoff",
]
