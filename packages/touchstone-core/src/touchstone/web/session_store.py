"""Persistent, encrypted browser-session store.

Solves the "MFA breaks headless automation" problem. Flow:

  1. Operator runs `touchstone session bootstrap looker_admin` once.
  2. Touchstone opens a *headed* (visible) browser, navigates to the login
     URL, and prints a one-line prompt: "Complete login (including MFA) in
     the browser window, then press ENTER here."
  3. Operator logs in interactively, completes Duo push / OAuth / TOTP, lands
     on the post-login page, presses ENTER.
  4. Touchstone captures Playwright `storage_state` (cookies + localStorage),
     encrypts it with the operator's key, and writes it to disk under the
     credential name.
  5. Subsequent headless calls load the encrypted state. The AI never sees
     cookies or credentials at any point.
  6. When a request lands on a login page (heuristic: URL/title contains
     "login" / "sign in" / known SSO redirector), Touchstone marks the
     session as stale and refuses further work until re-bootstrap.

Encryption: AES-GCM-256 via `cryptography`. Key derived from
`TOUCHSTONE_SESSION_KEY` (PBKDF2-HMAC-SHA256, 200k iterations, per-credential
salt). If the env var isn't set, we refuse to write — there is no fallback
to plaintext.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SESSION_DIR_DEFAULT = "~/.touchstone/web-sessions"


@dataclass(slots=True)
class StoredSession:
    credential_name: str
    storage_state: dict[str, Any]
    captured_at: str
    fingerprint: str   # hash of (user_agent, login_url) — for detecting reuse against wrong site


class SessionStoreError(Exception):
    pass


class SessionStore:
    """Encrypted on-disk store of Playwright storage_state per credential."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir or SESSION_DIR_DEFAULT).expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def save(self, credential_name: str, storage_state: dict[str, Any],
             fingerprint: str = "") -> Path:
        from datetime import UTC, datetime
        key = self._derive_key(credential_name)
        rec = StoredSession(
            credential_name=credential_name,
            storage_state=storage_state,
            captured_at=datetime.now(UTC).isoformat(),
            fingerprint=fingerprint,
        )
        plaintext = json.dumps({
            "credential_name": rec.credential_name,
            "storage_state": rec.storage_state,
            "captured_at": rec.captured_at,
            "fingerprint": rec.fingerprint,
        }).encode("utf-8")
        ciphertext = _aes_gcm_encrypt(key, plaintext)
        path = self._path_for(credential_name)
        # Write atomically: tmp + rename, restrictive perms.
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(ciphertext)
        os.chmod(tmp, 0o600)
        tmp.rename(path)
        return path

    def load(self, credential_name: str) -> StoredSession:
        path = self._path_for(credential_name)
        if not path.exists():
            raise SessionStoreError(
                f"no session stored for credential {credential_name!r}. "
                f"Run `touchstone session bootstrap {credential_name}` first."
            )
        key = self._derive_key(credential_name)
        plaintext = _aes_gcm_decrypt(key, path.read_bytes())
        data = json.loads(plaintext)
        return StoredSession(**data)

    def exists(self, credential_name: str) -> bool:
        return self._path_for(credential_name).exists()

    def delete(self, credential_name: str) -> None:
        path = self._path_for(credential_name)
        if path.exists():
            path.unlink()

    def list_sessions(self) -> list[str]:
        return sorted(p.stem for p in self.base_dir.glob("*.aesgcm"))

    def _path_for(self, name: str) -> Path:
        if not all(c.isalnum() or c in "_-" for c in name):
            raise SessionStoreError(f"unsafe credential name: {name!r}")
        return self.base_dir / f"{name}.aesgcm"

    def _derive_key(self, credential_name: str) -> bytes:
        passphrase = os.environ.get("TOUCHSTONE_SESSION_KEY")
        if not passphrase:
            raise SessionStoreError(
                "TOUCHSTONE_SESSION_KEY is not set. Touchstone refuses to "
                "encrypt/decrypt session state without an operator key — "
                "there is no plaintext fallback. Set the env var (operator "
                "machine) or a vault-backed value (server deployment)."
            )
        try:
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.primitives import hashes
        except ImportError as e:
            raise SessionStoreError(
                "cryptography not installed. Install with: "
                "pip install 'touchstone-core[web]'"
            ) from e
        # Per-credential salt so two creds with the same passphrase have
        # different keys.
        salt = b"touchstone/v1/" + credential_name.encode("utf-8")
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                         iterations=200_000)
        return kdf.derive(passphrase.encode("utf-8"))


def _aes_gcm_encrypt(key: bytes, plaintext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    aes = AESGCM(key)
    ct = aes.encrypt(nonce, plaintext, b"touchstone-session")
    return b"TS1" + nonce + ct


def _aes_gcm_decrypt(key: bytes, blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(b"TS1"):
        raise SessionStoreError("unknown session blob format")
    nonce, ct = blob[3:15], blob[15:]
    aes = AESGCM(key)
    return aes.decrypt(nonce, ct, b"touchstone-session")


# -- Login-page detection (for staleness checks) --------------------------

LOGIN_INDICATORS = (
    "/login", "/signin", "/sign-in", "/sso", "/auth", "/oauth", "/account/login",
    "okta.com", "auth0.com", "duo.com", "ping.com",
)
LOGIN_TITLES = ("sign in", "log in", "login", "authentication", "verify your identity")


def looks_like_login_page(url: str, title: str | None) -> bool:
    u = (url or "").lower()
    t = (title or "").lower()
    if any(ind in u for ind in LOGIN_INDICATORS):
        return True
    if any(ind in t for ind in LOGIN_TITLES):
        return True
    return False


# -- MFA challenge detection ----------------------------------------------

MFA_INDICATORS = (
    "duo", "duosecurity", "verify your identity", "2-step", "two-step",
    "two-factor", "security code", "authenticator", "push notification",
    "approve sign in", "verify it's you",
)


def looks_like_mfa_challenge(url: str, title: str | None, body_text: str | None = None) -> bool:
    blob = " ".join(filter(None, (url or "", title or "", body_text or ""))).lower()
    return any(ind in blob for ind in MFA_INDICATORS)
