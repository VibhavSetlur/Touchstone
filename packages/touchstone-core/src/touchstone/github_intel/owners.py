"""Find owners for a path or table.

Sources, in priority order:
  1. KnowledgeStore (manual + codeowners-seeded).
  2. CODEOWNERS in the repo (live fetch, cached).
  3. Top contributors to recent commits touching the path.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from touchstone.github_intel.api import GitHubAPI, GitHubError
from touchstone.knowledge.store import KnowledgeStore, Owner


@dataclass(slots=True)
class OwnerSuggestion:
    handle: str
    role: str
    source: str
    confidence: float


def find_owners(
    *,
    store: KnowledgeStore | None,
    api: GitHubAPI | None,
    repo: str | None,
    path: str,
) -> list[OwnerSuggestion]:
    suggestions: list[OwnerSuggestion] = []

    if store:
        for o in store.who_owns_path(path):
            suggestions.append(OwnerSuggestion(
                handle=o.owner, role=o.role, source=f"knowledge/{o.source}",
                confidence=0.95 if o.source == "manual" else 0.85,
            ))

    if not suggestions and api and repo:
        try:
            commits = api.commits_for_file(repo, path)
        except GitHubError:
            commits = []
        counts = Counter(c.get("author", {}).get("login")
                         for c in commits if c.get("author"))
        total = sum(counts.values()) or 1
        for handle, n in counts.most_common(3):
            if handle:
                suggestions.append(OwnerSuggestion(
                    handle=handle, role="contributor", source="git_blame",
                    confidence=min(0.7, n / total),
                ))

    return suggestions
