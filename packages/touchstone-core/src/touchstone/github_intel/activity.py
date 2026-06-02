"""Recent activity summarizers — "who changed X recently", "what merged this week"."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from touchstone.github_intel.api import GitHubAPI


@dataclass(slots=True)
class ChangeRecord:
    sha: str
    author: str
    when: str
    message: str
    files: list[str] = field(default_factory=list)


def recent_changes_to(api: GitHubAPI, repo: str, path: str, *, days: int = 30) -> list[ChangeRecord]:
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    commits = api.commits_for_file(repo, path, since=since)
    out: list[ChangeRecord] = []
    for c in commits:
        out.append(ChangeRecord(
            sha=c.get("sha", "")[:10],
            author=(c.get("author") or {}).get("login", "")
                or c.get("commit", {}).get("author", {}).get("name", ""),
            when=c.get("commit", {}).get("author", {}).get("date", ""),
            message=(c.get("commit", {}).get("message", "") or "").splitlines()[0][:200],
        ))
    return out


def who_changed(api: GitHubAPI, repo: str, path: str, *, days: int = 90) -> dict[str, int]:
    """Counter of {author: commit_count} for a path."""
    from collections import Counter
    changes = recent_changes_to(api, repo, path, days=days)
    return dict(Counter(c.author for c in changes if c.author).most_common(10))
