"""Readiness endpoint tests (BACKEND-03 §13/§16).

Liveness (`/health`) is unaffected by any of this — it is not touched or
retested here; see `tests/test_health.py`, still passing, still meaning
only "the process is running."
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from app.main import configure_app
from app.persistence.session import configure_database, dispose_engine


def _isolated_client() -> TestClient:
    settings = get_settings()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    configure_app(app, settings)
    from app.api.v1.router import api_v1_router

    app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)
    return TestClient(app, raise_server_exceptions=False)


def test_readiness_not_ready_when_database_unconfigured() -> None:
    dispose_engine()  # ensure no earlier test left a database configured
    client = _isolated_client()

    response = client.get("/api/v1/readiness")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": "unavailable"}


def test_readiness_not_ready_when_database_unreachable_and_leaks_nothing() -> None:
    # Port 1 is not a PostgreSQL port anywhere; this fails fast (connection
    # refused) without needing a real PostgreSQL server at all.
    configure_database("postgresql+psycopg://probe_user:probe_password@127.0.0.1:1/impulso_unreachable")
    try:
        client = _isolated_client()
        response = client.get("/api/v1/readiness")

        assert response.status_code == 503
        assert response.json() == {"status": "not_ready", "database": "unavailable"}
        assert "probe_user" not in response.text
        assert "probe_password" not in response.text
        assert "127.0.0.1" not in response.text
        assert "impulso_unreachable" not in response.text
    finally:
        dispose_engine()


@pytest.mark.postgres
def test_readiness_ready_when_database_available(postgres_engine: Engine) -> None:
    configure_database(str(postgres_engine.url))
    try:
        client = _isolated_client()
        response = client.get("/api/v1/readiness")

        assert response.status_code == 200
        assert response.json() == {"status": "ready", "database": "ok"}
    finally:
        dispose_engine()
