"""Session store encryption tests."""

from __future__ import annotations

import os

import pytest

from touchstone.web.session_store import (
    SessionStore,
    SessionStoreError,
    looks_like_login_page,
    looks_like_mfa_challenge,
)


@pytest.fixture
def store_dir(tmp_path):
    return str(tmp_path / "sessions")


def test_save_requires_key(monkeypatch, store_dir):
    monkeypatch.delenv("TOUCHSTONE_SESSION_KEY", raising=False)
    s = SessionStore(base_dir=store_dir)
    with pytest.raises(SessionStoreError):
        s.save("looker_admin", {"cookies": []})


def test_save_and_load_roundtrip(monkeypatch, store_dir):
    monkeypatch.setenv("TOUCHSTONE_SESSION_KEY", "test-passphrase-1234")
    s = SessionStore(base_dir=store_dir)
    state = {"cookies": [{"name": "sid", "value": "abc"}],
              "origins": [{"origin": "https://looker.acme.com"}]}
    s.save("looker_admin", state, fingerprint="abc123")
    loaded = s.load("looker_admin")
    assert loaded.storage_state == state
    assert loaded.fingerprint == "abc123"


def test_different_key_fails_to_decrypt(monkeypatch, store_dir):
    monkeypatch.setenv("TOUCHSTONE_SESSION_KEY", "key1")
    SessionStore(base_dir=store_dir).save("x", {"cookies": []})
    monkeypatch.setenv("TOUCHSTONE_SESSION_KEY", "key2")
    with pytest.raises(Exception):
        SessionStore(base_dir=store_dir).load("x")


def test_unsafe_credential_name_rejected(monkeypatch, store_dir):
    monkeypatch.setenv("TOUCHSTONE_SESSION_KEY", "k")
    s = SessionStore(base_dir=store_dir)
    with pytest.raises(SessionStoreError):
        s.save("../etc/passwd", {})


def test_login_page_detection():
    assert looks_like_login_page("https://acme.com/login", "Sign in")
    assert looks_like_login_page("https://acme.okta.com/signin/", None)
    assert not looks_like_login_page("https://acme.com/dashboards/42", "Daily Revenue")


def test_mfa_detection():
    assert looks_like_mfa_challenge("https://duo.com/frame", "Two-Step Verification")
    assert looks_like_mfa_challenge("https://acme.com/x", "Verify your identity")
    assert not looks_like_mfa_challenge("https://acme.com/x", "Dashboard")
