"""Typed application configuration.

Values are read from environment variables, and optionally from a local
``.env`` file (see ``.env.example``). No secrets are defined or defaulted
here — this stage has no authentication, no database, and no third-party
provider credentials to configure.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
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
