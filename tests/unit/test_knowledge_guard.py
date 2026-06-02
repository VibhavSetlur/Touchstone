"""Knowledge-store content guard tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from touchstone.knowledge.store import (
    KnowledgeContentError,
    KnowledgeStore,
    Note,
    _guard_content,
    _luhn,
)


def test_guard_passes_normal_note():
    _guard_content("Just a note about the orders table TZ bug.")


def test_guard_rejects_many_emails():
    body = "users:\n" + "\n".join(f"u{i}@example.com" for i in range(6))
    with pytest.raises(KnowledgeContentError):
        _guard_content(body)


def test_guard_rejects_many_phones():
    body = "callbacks: " + " ".join("415-555-12" + str(i).zfill(2) for i in range(6))
    with pytest.raises(KnowledgeContentError):
        _guard_content(body)


def test_guard_rejects_multiple_valid_card_numbers():
    cards = ["4242424242424242", "4111111111111111", "5555555555554444"]
    body = "test cards: " + " ".join(cards)
    assert all(_luhn(c) for c in cards)
    with pytest.raises(KnowledgeContentError):
        _guard_content(body)


def test_guard_rejects_oversize():
    big = "x" * (20 * 1024)
    with pytest.raises(KnowledgeContentError):
        _guard_content(big)


def test_store_refuses_pii_dump(tmp_path: Path):
    store = KnowledgeStore(tmp_path / "k.db")
    body = "list:\n" + "\n".join(f"u{i}@example.com" for i in range(10))
    with pytest.raises(KnowledgeContentError):
        store.add_note(Note(id=None, key="dump", body=body))
    store.close()
