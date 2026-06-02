"""Seed and refresh the knowledge store from GitHub.

Pulls:
  - CODEOWNERS → owners table.
  - Recently merged PRs → pr_digest table.
  - PR authors → owners (as `source='git_blame'`).

Run via `touchstone knowledge sync --repo owner/name`. Operator-controlled —
the AI assistant doesn't have GitHub write tokens.
"""

from __future__ import annotations

import os
import re
from typing import Any

from touchstone.knowledge.store import KnowledgeStore, Owner, PRDigest


def sync_codeowners(store: KnowledgeStore, repo: str, content: str | None = None,
                    fetch=None) -> int:
    """Parse a CODEOWNERS file and write rules to the owners table.

    Provide `content` to parse a local file, or pass `fetch=callable` to load
    from the repo. Returns the number of rules added.
    """
    if content is None:
        if fetch is None:
            from touchstone.knowledge.github_sync import _default_fetch
            fetch = _default_fetch
        content = fetch(repo, "CODEOWNERS") or fetch(repo, ".github/CODEOWNERS") \
                  or fetch(repo, "docs/CODEOWNERS") or ""

    added = 0
    for line in content.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        bits = re.split(r"\s+", line)
        if len(bits) < 2:
            continue
        path = bits[0]
        owners = [b.lstrip("@") for b in bits[1:] if b]
        norm_path = path.lstrip("/").rstrip("/")
        for o in owners:
            store.add_owner(Owner(
                key=f"path:{norm_path}", owner=o,
                role="owner", source="codeowners",
            ))
            added += 1
    return added


def sync_recent_prs(store: KnowledgeStore, repo: str, *, limit: int = 100,
                    fetch=None) -> int:
    """Pull the last N merged PRs and write digests."""
    if fetch is None:
        fetch = _default_pr_fetch
    prs = fetch(repo, limit=limit)
    n = 0
    for p in prs:
        store.add_pr(PRDigest(
            repo=repo,
            number=p["number"],
            title=p["title"],
            author=p.get("user", {}).get("login", ""),
            merged_at=p.get("merged_at") or "",
            files_touched=p.get("_files", []),
            summary=p.get("body", "")[:512] if p.get("body") else "",
        ))
        store.add_owner(Owner(
            key=f"repo:{repo}", owner=p.get("user", {}).get("login", ""),
            role="contributor", source="git_blame",
        ))
        n += 1
    return n


def _default_fetch(repo: str, path: str) -> str | None:
    import base64
    import json as _json
    import urllib.request

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "touchstone"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    try:
        with urllib.request.urlopen(  # noqa: S310
            urllib.request.Request(url, headers=headers), timeout=15
        ) as resp:
            data = _json.loads(resp.read())
    except Exception:
        return None
    if data.get("encoding") != "base64":
        return None
    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


def _default_pr_fetch(repo: str, *, limit: int = 100) -> list[dict[str, Any]]:
    import json as _json
    import urllib.request

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "touchstone"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{repo}/pulls?state=closed&per_page={min(limit, 100)}"
    try:
        with urllib.request.urlopen(  # noqa: S310
            urllib.request.Request(url, headers=headers), timeout=30
        ) as resp:
            prs = _json.loads(resp.read())
    except Exception:
        return []

    merged = [p for p in prs if p.get("merged_at")]
    for p in merged:
        files_url = f"https://api.github.com/repos/{repo}/pulls/{p['number']}/files"
        try:
            with urllib.request.urlopen(  # noqa: S310
                urllib.request.Request(files_url, headers=headers), timeout=15
            ) as r:
                files = _json.loads(r.read())
            p["_files"] = [f["filename"] for f in files]
        except Exception:
            p["_files"] = []
    return merged
