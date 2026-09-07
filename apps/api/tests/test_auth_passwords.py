"""Password hashing tests — pure unit tests, no database needed."""

from __future__ import annotations

from app.auth.security import hash_password, hash_token, verify_password


def test_hash_is_not_the_plaintext_password() -> None:
    password = "correct horse battery staple"
    hashed = hash_password(password)
    assert hashed != password
    assert password not in hashed


def test_hash_uses_argon2id() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")


def test_correct_password_verifies() -> None:
    password = "correct horse battery staple"
    hashed = hash_password(password)
    assert verify_password(password, hashed) is True


def test_wrong_password_is_rejected() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password entirely", hashed) is False


def test_same_password_produces_different_hashes_each_time() -> None:
    """Argon2id salts each hash independently — two hashes of the same
    password must never be byte-for-byte identical."""
    password = "correct horse battery staple"
    first = hash_password(password)
    second = hash_password(password)
    assert first != second
    assert verify_password(password, first) is True
    assert verify_password(password, second) is True


def test_token_hash_is_deterministic_and_one_way() -> None:
    token = "some-opaque-session-token-value"
    digest = hash_token(token)
    assert hash_token(token) == digest  # deterministic
    assert digest != token  # not reversible/identity
    assert len(digest) == 64  # SHA-256 hex digest length


def test_different_tokens_hash_differently() -> None:
    assert hash_token("token-a") != hash_token("token-b")
