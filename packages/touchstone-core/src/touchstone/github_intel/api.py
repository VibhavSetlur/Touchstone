"""Tiny GitHub REST client.

We avoid pulling in PyGithub / githubkit — both are large and we only need
a handful of endpoints. The client honors GITHUB_TOKEN, falls back to
unauthenticated (heavily rate-limited), and never logs the token.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class GitHubError(Exception):
    pass


class GitHubAPI:
    def __init__(self, token: str | None = None, base_url: str = "https://api.github.com") -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN")
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "User-Agent": "touchstone",
             "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def get(self, path: str, params: dict[str, str | int] | None = None) -> Any:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers())
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 403 and "rate limit" in (e.headers.get("X-RateLimit-Remaining") or ""):
                    raise GitHubError("GitHub rate limit hit. Set GITHUB_TOKEN.") from None
                if e.code in (502, 503, 504) and attempt < 2:
                    time.sleep(1.5 ** attempt)
                    continue
                raise GitHubError(f"{e.code} {e.reason} — {path}") from None
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(1.5 ** attempt)
                    continue
                raise GitHubError(str(e)) from None
        raise GitHubError(f"failed: {path}")

    def paginate(self, path: str, params: dict[str, str | int] | None = None, *, max_pages: int = 10) -> list[Any]:
        out: list[Any] = []
        params = dict(params or {})
        params.setdefault("per_page", 100)
        for page in range(1, max_pages + 1):
            params["page"] = page
            batch = self.get(path, params)
            if not isinstance(batch, list) or not batch:
                break
            out.extend(batch)
            if len(batch) < params["per_page"]:
                break
        return out

    # convenience wrappers ----
    def pull(self, repo: str, number: int) -> dict[str, Any]:
        return self.get(f"/repos/{repo}/pulls/{number}")

    def pull_files(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self.paginate(f"/repos/{repo}/pulls/{number}/files")

    def pull_reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self.paginate(f"/repos/{repo}/pulls/{number}/reviews")

    def commits_for_file(self, repo: str, path: str, *, since: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {"path": path}
        if since:
            params["since"] = since
        return self.paginate(f"/repos/{repo}/commits", params=params, max_pages=3)

    def repo_contributors(self, repo: str) -> list[dict[str, Any]]:
        return self.paginate(f"/repos/{repo}/contributors", max_pages=3)

    def issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self.paginate(f"/repos/{repo}/issues/{number}/comments")

    def open_issues(self, repo: str, label: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {"state": "open"}
        if label:
            params["labels"] = label
        return self.paginate(f"/repos/{repo}/issues", params=params, max_pages=3)
