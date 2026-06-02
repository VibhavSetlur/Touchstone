"""GitHub intelligence — read-only signal extraction.

Higher-level than `touchstone-cli/github_fetch.py`. Used by playbooks and the
MCP server to answer "who changed X", "who reviews Y", "what's blocking Z".

All operations are READ-ONLY. The AI assistant cannot post comments, merge
PRs, or modify state through these tools. Write operations (posting a PR
review) are deliberately scoped to the Probot app, which has its own
short-lived installation tokens and a different threat model.
"""

from touchstone.github_intel.api import GitHubAPI, GitHubError
from touchstone.github_intel.owners import find_owners
from touchstone.github_intel.activity import recent_changes_to, who_changed

__all__ = [
    "GitHubAPI", "GitHubError",
    "find_owners", "recent_changes_to", "who_changed",
]
