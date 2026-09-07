"""Password hashing and session-token generation/hashing.

These are deliberately two different tools for two different threats:

- **Passwords** are low-entropy, human-chosen secrets that an attacker
  with a stolen hash can try to guess offline. Argon2id (via ``pwdlib``)
  is deliberately slow and memory-hard specifically to make that
  offline guessing expensive.
- **Session tokens** (and the CSRF secret) are generated with
  ``secrets.token_urlsafe`` — already 256 bits of cryptographically
  random entropy, nothing to "guess" in any practical sense. Hashing
  them with a slow KDF would only waste CPU on every single request;
  what the hash defends against is different: a stolen database dump
  should not hand an attacker a working session. A single fast
  cryptographic digest (SHA-256) is the correct, standard tool for that.

Never confuse the two — passwords go through ``hash_password``/
``verify_password`` (slow, salted, Argon2id); tokens go through
``hash_token`` (fast, deterministic, SHA-256).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from pwdlib import PasswordHash

_password_hasher = PasswordHash.recommended()  # Argon2id, tuned defaults

SESSION_TOKEN_ENTROPY_BYTES = 32  # 256 bits


def hash_password(plain_password: str) -> str:
    return _password_hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return _password_hasher.verify(plain_password, password_hash)


def generate_random_token(*, entropy_bytes: int = SESSION_TOKEN_ENTROPY_BYTES) -> str:
    """Used for both session tokens and CSRF secrets — both need the
    same property (unguessable, high-entropy, URL/cookie/header-safe),
    just with different storage treatment afterward."""
    return secrets.token_urlsafe(entropy_bytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


# A fixed, precomputed Argon2id hash of a value nobody will ever type,
# used only to make a "no such user" login attempt perform the same
# `verify_password` work as a real one — so the two cases are not
# distinguishable by response timing (see app/auth/service.py).
_DUMMY_PASSWORD_HASH = hash_password("impulso-dummy-hash-for-timing-parity-only")


def verify_against_dummy_hash(plain_password: str) -> None:
    verify_password(plain_password, _DUMMY_PASSWORD_HASH)
