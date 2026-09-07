"""Typed application configuration.

Values are read from environment variables, and optionally from a local
``.env`` file (see ``.env.example``). No secrets are defined or defaulted
here — this stage has no authentication and no third-party provider
credentials to configure. ``DATABASE_URL``/``TEST_DATABASE_URL`` are read
as plain strings with no default value: there is no "convenient" local
default, because a wrong-but-working default is exactly how a test
accidentally points at development data, or development accidentally
points at production. See ``app.persistence`` for how these are used.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "Impulso API"
    APP_ENV: Environment = "development"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = True

    # No default. Development/test may run without a database configured
    # at all (most of this API's own test suite does) — in that case the
    # readiness endpoint reports "not ready" rather than the app failing
    # to start. Production fails closed: see `_require_database_url_in_production`.
    DATABASE_URL: str | None = None

    # A completely separate setting from DATABASE_URL — tests must never
    # silently fall back to the development database. See
    # `app.persistence.testing.assert_safe_test_database_url` for the
    # additional runtime guard applied before any destructive test touches
    # whatever this points at.
    TEST_DATABASE_URL: str | None = None

    @model_validator(mode="after")
    def _require_database_url_in_production(self) -> "Settings":
        if self.APP_ENV == "production" and not self.DATABASE_URL:
            raise ValueError(
                "DATABASE_URL is required when APP_ENV=production. Refusing "
                "to start with no configured database in production."
            )
        return self

    # `NoDecode` tells pydantic-settings' env source not to attempt its
    # default JSON-decoding for this list-typed field (which otherwise
    # hard-fails with a SettingsError on a plain comma-separated string
    # before our own validator ever runs). With it, the raw env-var
    # string reaches `_split_comma_separated` below untouched.
    CORS_ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    @field_validator("CORS_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        """Allow ``CORS_ALLOWED_ORIGINS=a,b,c`` in a plain .env file, in
        addition to passing an explicit Python list directly (e.g. in
        tests: ``Settings(CORS_ALLOWED_ORIGINS=["a", "b"])``)."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("CORS_ALLOWED_ORIGINS")
    @classmethod
    def _forbid_wildcard_origin(cls, value: list[str]) -> list[str]:
        if "*" in value:
            raise ValueError(
                "CORS_ALLOWED_ORIGINS must not contain '*'. List explicit "
                "origins instead — a wildcard origin combined with "
                "credentialed CORS requests is unsafe."
            )
        return value

    # Session cookie (BACKEND-04). No secret lives here — the cookie
    # carries only an opaque, high-entropy token; see app/auth/security.py.
    SESSION_COOKIE_NAME: str = "impulso_session"
    SESSION_COOKIE_SECURE: bool = False
    SESSION_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    SESSION_TTL_SECONDS: int = 60 * 60 * 24 * 14  # 14 days

    @model_validator(mode="after")
    def _require_secure_cookie_in_production(self) -> "Settings":
        if self.APP_ENV == "production" and not self.SESSION_COOKIE_SECURE:
            raise ValueError(
                "SESSION_COOKIE_SECURE must be true when APP_ENV=production. "
                "Refusing to start with a session cookie that browsers would "
                "send over plain HTTP in production."
            )
        return self

    @model_validator(mode="after")
    def _samesite_none_requires_secure(self) -> "Settings":
        if self.SESSION_COOKIE_SAMESITE == "none" and not self.SESSION_COOKIE_SECURE:
            raise ValueError(
                "SESSION_COOKIE_SAMESITE=none requires SESSION_COOKIE_SECURE=true "
                "— this is a browser requirement (SameSite=None cookies without "
                "Secure are rejected outright), not just an application policy."
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache
def get_settings() -> Settings:
    """FastAPI dependency: returns a cached ``Settings`` singleton.

    This is the first entry in what will become a small family of request
    dependencies (settings, current user, workspace context, database
    session, services). Only ``settings`` is implemented in this stage —
    the others are intentionally deferred to the phases that introduce
    real authentication and persistence.
    """
    return Settings()
