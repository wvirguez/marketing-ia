"""Shared fixtures for auth/tenancy tests that need a real, isolated app
instance wired to a real PostgreSQL database — never the shared `app`
singleton, and never SQLite.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.api.v1.router import api_v1_router
from app.core.config import Settings, get_settings
from app.main import configure_app
from app.persistence.session import configure_database, dispose_engine


def _build_auth_app(settings: Settings) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    configure_app(app, settings)
    app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)
    return app


@pytest.fixture()
def auth_client(postgres_engine: Engine) -> Iterator[TestClient]:
    """A TestClient wired to a real database, with cookie persistence
    across requests (httpx's default) — exactly what a register -> login
    -> session -> logout flow needs to look like real browser behavior."""
    settings = get_settings()
    configure_database(
         postgres_engine.url.render_as_string(hide_password=False),
         echo=False,
     )
    try:
        app = _build_auth_app(settings)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        dispose_engine()


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


def register_payload(**overrides: object) -> dict:
    payload = {
        "email": unique_email(),
        "password": "correct horse battery staple",
        "display_name": "Test User",
    }
    payload.update(overrides)
    return payload
