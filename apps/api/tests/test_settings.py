"""Focused tests for typed settings behavior claimed by BACKEND-02.

These construct `Settings` directly (never through the cached
`get_settings()`), so each test is independent of any previously-cached
singleton and of any other test's environment mutations.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("env", ["development", "test", "production"])
def test_accepts_each_supported_environment(env: str) -> None:
    # production additionally requires DATABASE_URL and a secure session
    # cookie — see the dedicated tests below; those are separate concerns
    # from "is this a recognized environment value."
    settings = Settings(
        APP_ENV=env, DATABASE_URL="postgresql+psycopg://user@localhost/db", SESSION_COOKIE_SECURE=True
    )
    assert settings.APP_ENV == env


def test_rejects_unsupported_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(APP_ENV="staging")


def test_production_requires_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(APP_ENV="production", DATABASE_URL=None, SESSION_COOKIE_SECURE=True)


def test_production_accepts_configured_database_url() -> None:
    settings = Settings(
        APP_ENV="production", DATABASE_URL="postgresql+psycopg://user@localhost/db", SESSION_COOKIE_SECURE=True
    )
    assert settings.DATABASE_URL == "postgresql+psycopg://user@localhost/db"


def test_production_requires_secure_session_cookie() -> None:
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="production",
            DATABASE_URL="postgresql+psycopg://user@localhost/db",
            SESSION_COOKIE_SECURE=False,
        )


def test_samesite_none_requires_secure_cookie() -> None:
    with pytest.raises(ValidationError):
        Settings(SESSION_COOKIE_SAMESITE="none", SESSION_COOKIE_SECURE=False)


def test_samesite_none_accepted_with_secure_cookie() -> None:
    settings = Settings(SESSION_COOKIE_SAMESITE="none", SESSION_COOKIE_SECURE=True)
    assert settings.SESSION_COOKIE_SAMESITE == "none"


def test_session_cookie_defaults_are_development_safe() -> None:
    settings = Settings()
    assert settings.SESSION_COOKIE_NAME == "impulso_session"
    assert settings.SESSION_COOKIE_SAMESITE == "lax"
    assert settings.SESSION_TTL_SECONDS > 0


def test_development_and_test_allow_missing_database_url() -> None:
    assert Settings(APP_ENV="development", DATABASE_URL=None).DATABASE_URL is None
    assert Settings(APP_ENV="test", DATABASE_URL=None).DATABASE_URL is None


def test_accepts_explicit_origin_list_via_constructor() -> None:
    settings = Settings(CORS_ALLOWED_ORIGINS=["http://localhost:3000", "http://localhost:3001"])
    assert settings.CORS_ALLOWED_ORIGINS == ["http://localhost:3000", "http://localhost:3001"]


def test_rejects_wildcard_origin_via_constructor() -> None:
    with pytest.raises(ValidationError):
        Settings(CORS_ALLOWED_ORIGINS=["*"])


def test_rejects_wildcard_mixed_with_real_origins() -> None:
    with pytest.raises(ValidationError):
        Settings(CORS_ALLOWED_ORIGINS=["http://localhost:3000", "*"])


def test_cors_origins_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    assert Settings().CORS_ALLOWED_ORIGINS == ["http://localhost:3000"]


def test_cors_origins_from_comma_separated_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """This is the real claim BACKEND-02 made: a plain comma-separated
    string in the environment (as one would write in a .env file) must
    work through pydantic-settings' actual env-loading path — not just
    when the validator is fed a Python list directly by a test."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:3001")
    settings = Settings()
    assert settings.CORS_ALLOWED_ORIGINS == ["http://localhost:3000", "http://localhost:3001"]


def test_cors_origins_env_var_trims_whitespace_and_drops_empties(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", " http://localhost:3000 , , http://localhost:3001 ")
    settings = Settings()
    assert settings.CORS_ALLOWED_ORIGINS == ["http://localhost:3000", "http://localhost:3001"]


def test_wildcard_via_env_var_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
    with pytest.raises(ValidationError):
        Settings()
