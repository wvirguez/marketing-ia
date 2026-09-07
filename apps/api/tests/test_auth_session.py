"""Session model tests (BACKEND-04 §32) — real database required."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.models import AuthSession
from app.auth.security import hash_token
from tests.authtest import register_payload

pytestmark = pytest.mark.postgres


def test_login_sets_httponly_cookie(auth_client: TestClient) -> None:
    payload = register_payload()
    auth_client.post("/api/v1/auth/register", json=payload)
    auth_client.cookies.clear()

    response = auth_client.post("/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]})

    assert response.status_code == 200
    assert "httponly" in response.headers.get("set-cookie", "").lower()
    assert "impulso_session" in auth_client.cookies


def test_raw_session_token_is_never_stored_in_the_database(auth_client: TestClient, db_session) -> None:
    auth_client.post("/api/v1/auth/register", json=register_payload())
    raw_token = auth_client.cookies["impulso_session"]

    stored_hashes = db_session.execute(select(AuthSession.token_hash)).scalars().all()
    assert raw_token not in stored_hashes
    assert hash_token(raw_token) in stored_hashes


def test_valid_session_authenticates(auth_client: TestClient) -> None:
    auth_client.post("/api/v1/auth/register", json=register_payload())

    response = auth_client.get("/api/v1/auth/session")

    assert response.status_code == 200
    assert set(response.json().keys()) == {"user", "workspace", "membership"}


def test_missing_session_is_rejected(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/auth/session")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_unknown_session_token_is_rejected(auth_client: TestClient) -> None:
    auth_client.cookies.set("impulso_session", "totally-made-up-token-value")

    response = auth_client.get("/api/v1/auth/session")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_expired_session_is_rejected(auth_client: TestClient, postgres_engine) -> None:
    auth_client.post("/api/v1/auth/register", json=register_payload())
    raw_token = auth_client.cookies["impulso_session"]

    # `db_session` (used elsewhere in this file) deliberately never
    # really commits across connections — it is isolated on purpose (see
    # tests/dbtest.py). Proving the *app's own, separate* connection sees
    # this change requires a real, immediately-committed transaction on
    # the shared engine instead.
    with postgres_engine.begin() as connection:
        connection.execute(
            AuthSession.__table__.update()
            .where(AuthSession.token_hash == hash_token(raw_token))
            .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )

    response = auth_client.get("/api/v1/auth/session")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SESSION_EXPIRED"


def test_logout_revokes_the_session(auth_client: TestClient, db_session) -> None:
    auth_client.post("/api/v1/auth/register", json=register_payload())
    raw_token = auth_client.cookies["impulso_session"]
    csrf_token = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]

    response = auth_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token})
    assert response.status_code == 200

    stored = db_session.execute(
        select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token))
    ).scalar_one()
    assert stored.revoked_at is not None


def test_revoked_session_no_longer_authenticates(auth_client: TestClient) -> None:
    auth_client.post("/api/v1/auth/register", json=register_payload())
    csrf_token = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]
    auth_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token})

    response = auth_client.get("/api/v1/auth/session")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_logout_clears_the_cookie(auth_client: TestClient) -> None:
    auth_client.post("/api/v1/auth/register", json=register_payload())
    csrf_token = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]

    response = auth_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token})

    set_cookie = response.headers.get("set-cookie", "")
    assert "impulso_session=" in set_cookie
    # An expired/blank cookie value directs the browser to delete it.
    assert 'impulso_session=""' in set_cookie or "impulso_session=;" in set_cookie or 'Max-Age=0' in set_cookie
