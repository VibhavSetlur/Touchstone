"""Knowledge store — persistent context for QA work.

What it holds:
  - **Notes**: free-form notes attached to a key (table name, dashboard URL,
    repo path). "The `orders` table has a known timezone quirk on Friday
    night batches — see PR #1421."
  - **Owners**: who owns what (parsed from CODEOWNERS, augmented by humans).
  - **Tasks**: open follow-ups the AI tracked from prior conversations.
  - **Decisions**: ADR-style "we decided X for reason Y on date Z" records.
  - **PR digest**: a rolling summary of recent merged PRs, indexed by file
    they touched, so "who changed `customers.email`'s type recently?" works.

What it does NOT hold:
  - Database row data. Knowledge is metadata, not warehouse data.
  - Credentials. Same rule as the rest of Touchstone — never plaintext.
"""

from touchstone.knowledge.store import KnowledgeStore, Note, Owner, Task, Decision, PRDigest

__all__ = [
    "KnowledgeStore", "Note", "Owner", "Task", "Decision", "PRDigest",
]
