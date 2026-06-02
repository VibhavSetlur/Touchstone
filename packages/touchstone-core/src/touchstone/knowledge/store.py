"""SQLite-backed knowledge store.

One file (`~/.touchstone/knowledge.db` by default). Operator-owned, AI-readable
(via MCP) and AI-writable (with audit). Designed to be diffable / portable —
`sqlite3 knowledge.db .dump` produces a text snapshot you can check into a
private repo.

Optional vector index lives in `knowledge.vector` (separate module) — it's
opt-in because it pulls in a much larger dep tree (sentence-transformers).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Note:
    id: int | None
    key: str            # the thing this note is about: "table:orders" / "repo:acme/x" / "dashboard:looker/123"
    body: str
    tags: list[str] = field(default_factory=list)
    author: str = ""
    created_at: str = ""


@dataclass(slots=True)
class Owner:
    key: str            # "path:models/marts/" or "table:orders"
    owner: str          # github handle, slack id, or freeform
    role: str = "owner" # "owner" | "reviewer" | "subject-matter-expert"
    source: str = "manual"  # "manual" | "codeowners" | "git_blame"


@dataclass(slots=True)
class Task:
    id: int | None
    title: str
    body: str = ""
    status: str = "open"  # "open" | "in_progress" | "done" | "blocked"
    owner: str | None = None
    due_at: str | None = None
    related: list[str] = field(default_factory=list)  # links to other keys
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class Decision:
    id: int | None
    title: str
    rationale: str
    decided_at: str = ""
    decided_by: str = ""
    affects: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PRDigest:
    repo: str
    number: int
    title: str
    author: str
    merged_at: str
    files_touched: list[str] = field(default_factory=list)
    summary: str = ""


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    author TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_key ON notes(key);

CREATE TABLE IF NOT EXISTS owners (
    key TEXT NOT NULL,
    owner TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'owner',
    source TEXT NOT NULL DEFAULT 'manual',
    PRIMARY KEY (key, owner, role)
);
CREATE INDEX IF NOT EXISTS idx_owners_owner ON owners(owner);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    owner TEXT,
    due_at TEXT,
    related TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    rationale TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    decided_by TEXT NOT NULL DEFAULT '',
    affects TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS pr_digest (
    repo TEXT NOT NULL,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    merged_at TEXT NOT NULL,
    files_touched TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (repo, number)
);
CREATE INDEX IF NOT EXISTS idx_pr_files ON pr_digest(repo);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    body, key UNINDEXED, content='notes', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, body, key) VALUES (new.id, new.body, new.key);
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, body, key) VALUES('delete', old.id, old.body, old.key);
    INSERT INTO notes_fts(rowid, body, key) VALUES (new.id, new.body, new.key);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, body, key) VALUES('delete', old.id, old.body, old.key);
END;
"""


class KnowledgeStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    # -- notes ----
    def add_note(self, note: Note) -> Note:
        note.created_at = note.created_at or datetime.now(UTC).isoformat()
        cur = self._conn.execute(
            "INSERT INTO notes(key, body, tags, author, created_at) VALUES (?, ?, ?, ?, ?)",
            (note.key, note.body, json.dumps(note.tags), note.author, note.created_at),
        )
        self._conn.commit()
        note.id = cur.lastrowid
        return note

    def notes_for(self, key: str) -> list[Note]:
        rows = self._conn.execute(
            "SELECT id, key, body, tags, author, created_at FROM notes WHERE key = ? ORDER BY created_at DESC",
            (key,),
        ).fetchall()
        return [Note(id=r[0], key=r[1], body=r[2], tags=json.loads(r[3]),
                     author=r[4], created_at=r[5]) for r in rows]

    def search_notes(self, q: str, limit: int = 20) -> list[Note]:
        rows = self._conn.execute(
            """SELECT n.id, n.key, n.body, n.tags, n.author, n.created_at
               FROM notes_fts f JOIN notes n ON n.id = f.rowid
               WHERE notes_fts MATCH ? LIMIT ?""",
            (q, limit),
        ).fetchall()
        return [Note(id=r[0], key=r[1], body=r[2], tags=json.loads(r[3]),
                     author=r[4], created_at=r[5]) for r in rows]

    # -- owners ----
    def add_owner(self, owner: Owner) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO owners(key, owner, role, source) VALUES (?, ?, ?, ?)",
            (owner.key, owner.owner, owner.role, owner.source),
        )
        self._conn.commit()

    def owners_for(self, key: str) -> list[Owner]:
        rows = self._conn.execute(
            "SELECT key, owner, role, source FROM owners WHERE key = ?", (key,),
        ).fetchall()
        return [Owner(key=r[0], owner=r[1], role=r[2], source=r[3]) for r in rows]

    def who_owns_path(self, path: str) -> list[Owner]:
        """Walk path prefixes (deepest match first) so `models/marts/orders.sql`
        finds an owner at `models/marts/` or `models/` if no exact match."""
        parts = path.strip("/").split("/")
        candidates = []
        for i in range(len(parts), 0, -1):
            candidates.append("path:" + "/".join(parts[:i]))
        for c in candidates:
            owners = self.owners_for(c)
            if owners:
                return owners
        return []

    # -- tasks ----
    def add_task(self, task: Task) -> Task:
        now = datetime.now(UTC).isoformat()
        task.created_at = task.created_at or now
        task.updated_at = now
        cur = self._conn.execute(
            "INSERT INTO tasks(title, body, status, owner, due_at, related, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (task.title, task.body, task.status, task.owner, task.due_at,
             json.dumps(task.related), task.created_at, task.updated_at),
        )
        self._conn.commit()
        task.id = cur.lastrowid
        return task

    def update_task_status(self, task_id: int, status: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.now(UTC).isoformat(), task_id),
        )
        self._conn.commit()

    def open_tasks(self, owner: str | None = None) -> list[Task]:
        if owner:
            rows = self._conn.execute(
                "SELECT id, title, body, status, owner, due_at, related, created_at, updated_at "
                "FROM tasks WHERE status IN ('open', 'in_progress') AND owner = ? ORDER BY due_at",
                (owner,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, title, body, status, owner, due_at, related, created_at, updated_at "
                "FROM tasks WHERE status IN ('open', 'in_progress') ORDER BY due_at"
            ).fetchall()
        return [Task(id=r[0], title=r[1], body=r[2], status=r[3], owner=r[4],
                     due_at=r[5], related=json.loads(r[6]),
                     created_at=r[7], updated_at=r[8]) for r in rows]

    # -- decisions ----
    def add_decision(self, d: Decision) -> Decision:
        d.decided_at = d.decided_at or datetime.now(UTC).isoformat()
        cur = self._conn.execute(
            "INSERT INTO decisions(title, rationale, decided_at, decided_by, affects) "
            "VALUES (?, ?, ?, ?, ?)",
            (d.title, d.rationale, d.decided_at, d.decided_by, json.dumps(d.affects)),
        )
        self._conn.commit()
        d.id = cur.lastrowid
        return d

    def decisions_affecting(self, key: str) -> list[Decision]:
        rows = self._conn.execute(
            "SELECT id, title, rationale, decided_at, decided_by, affects "
            "FROM decisions WHERE affects LIKE ?", (f'%"{key}"%',),
        ).fetchall()
        return [Decision(id=r[0], title=r[1], rationale=r[2], decided_at=r[3],
                         decided_by=r[4], affects=json.loads(r[5])) for r in rows]

    # -- PR digest ----
    def add_pr(self, pr: PRDigest) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO pr_digest(repo, number, title, author, merged_at, files_touched, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pr.repo, pr.number, pr.title, pr.author, pr.merged_at,
             json.dumps(pr.files_touched), pr.summary),
        )
        self._conn.commit()

    def prs_touching(self, path_fragment: str, limit: int = 20) -> list[PRDigest]:
        rows = self._conn.execute(
            "SELECT repo, number, title, author, merged_at, files_touched, summary "
            "FROM pr_digest WHERE files_touched LIKE ? ORDER BY merged_at DESC LIMIT ?",
            (f'%"{path_fragment}%', limit),
        ).fetchall()
        return [PRDigest(repo=r[0], number=r[1], title=r[2], author=r[3],
                         merged_at=r[4], files_touched=json.loads(r[5]),
                         summary=r[6]) for r in rows]

    def close(self) -> None:
        self._conn.close()
