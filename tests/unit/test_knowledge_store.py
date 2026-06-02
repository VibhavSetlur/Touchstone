"""Knowledge store tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from touchstone.knowledge.store import KnowledgeStore, Note, Owner, Task, Decision


@pytest.fixture
def store(tmp_path: Path):
    s = KnowledgeStore(tmp_path / "k.db")
    yield s
    s.close()


def test_notes_roundtrip(store):
    n = store.add_note(Note(id=None, key="table:orders", body="TZ quirk on Fridays",
                             tags=["timezone"], author="alice"))
    assert n.id is not None
    notes = store.notes_for("table:orders")
    assert len(notes) == 1
    assert notes[0].body == "TZ quirk on Fridays"


def test_notes_full_text_search(store):
    store.add_note(Note(id=None, key="table:orders", body="timezone bug on Friday",
                         author="a"))
    store.add_note(Note(id=None, key="table:customers", body="email column has whitespace",
                         author="b"))
    hits = store.search_notes("timezone")
    assert len(hits) == 1
    assert hits[0].key == "table:orders"


def test_owner_path_walk(store):
    store.add_owner(Owner(key="path:models/marts", owner="@alice", role="owner"))
    owners = store.who_owns_path("models/marts/orders.sql")
    assert any(o.owner == "@alice" for o in owners)


def test_task_lifecycle(store):
    t = store.add_task(Task(id=None, title="Verify revenue dashboard",
                             owner="@bob", related=["dashboard:looker/42"]))
    open_tasks = store.open_tasks()
    assert any(x.id == t.id for x in open_tasks)
    store.update_task_status(t.id, "done")
    open_after = store.open_tasks()
    assert not any(x.id == t.id for x in open_after)


def test_decision_recall(store):
    d = store.add_decision(Decision(
        id=None, title="Drop legacy_users",
        rationale="Unused since 2024-Q3 migration",
        decided_by="@team-data",
        affects=["table:legacy_users"],
    ))
    found = store.decisions_affecting("table:legacy_users")
    assert any(x.id == d.id for x in found)
