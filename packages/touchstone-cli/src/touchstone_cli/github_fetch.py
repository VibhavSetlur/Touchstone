"""Fetch SQL file changes from a GitHub PR via the REST API.

Uses GITHUB_TOKEN if present, otherwise unauthenticated (rate-limited).
Reads only — never writes back from the CLI; PR comments are posted by the
touchstone-github app, which is a separate process with PR-write scope.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from touchstone.qa.pr import FileChange


SQL_EXTENSIONS = (".sql",)
SQL_LIKE_PATHS = ("models/", "migrations/", "macros/", "snapshots/", "seeds/")


def fetch_pr_sql_changes(repo: str, pr_number: int) -> list[FileChange]:
    """Return the list of file changes that look SQL-ish."""

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "touchstone"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    files = _api_get(f"https://api.github.com/repos/{repo}/pulls/{pr_number}/files", headers)
    pr = _api_get(f"https://api.github.com/repos/{repo}/pulls/{pr_number}", headers)
    base_sha = pr["base"]["sha"]
    head_sha = pr["head"]["sha"]

    changes: list[FileChange] = []
    for f in files:
        path = f["filename"]
        if not (path.endswith(SQL_EXTENSIONS) or any(s in path for s in SQL_LIKE_PATHS)):
            continue
        before = _fetch_blob(repo, base_sha, path, headers) if f["status"] != "added" else ""
        after = _fetch_blob(repo, head_sha, path, headers) if f["status"] != "removed" else ""
        changes.append(FileChange(path=path, before_sql=before, after_sql=after))
    return changes


def _api_get(url: str, headers: dict[str, str]):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub API error ({e.code}): {e.reason} — {url}") from None


def _fetch_blob(repo: str, sha: str, path: str, headers: dict[str, str]) -> str:
    url = (
        f"https://api.github.com/repos/{repo}/contents/{urllib.parse.quote(path)}"
        f"?ref={sha}"
    )
    try:
        data = _api_get(url, headers)
    except RuntimeError:
        return ""
    if data.get("encoding") != "base64":
        return ""
    try:
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except (KeyError, ValueError):
        return ""
