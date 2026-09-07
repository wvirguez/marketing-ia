"""Registration flow + atomicity (BACKEND-04 §15/§26/§35).

All marked `postgres` — real database required.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth.models import AuthSession
from app.users.models import User
from app.workspaces.models import Membership, Organization, Workspace
from tests.authtest import register_payload

pytestmark = pytest.mark.postgres


def _count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def test_register_returns_safe_public_objects(auth_client: TestClient) -> None:
    response = auth_client.post("/api/v1/auth/register", json=register_payload(display_name="Ada Lovelace"))

    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"user", "workspace", "membership"}
    assert body["user"]["display_name"] == "Ada Lovelace"
    assert body["membership"]["role"] == "OWNER"

    # No password hash, no raw DB id, no session token anywhere in the body.
    raw_body_text = response.text
    assert "password_hash" not in raw_body_text
    assert "argon2" not in raw_body_text
    # Public ids look like PREFIX-XXXX, never a bare UUID.
    assert body["user"]["id"].startswith("USR-")
    assert body["workspace"]["id"].startswith("WKS-")


def test_register_sets_httponly_session_cookie_and_no_token_in_body(auth_client: TestClient) -> None:
    response = auth_client.post("/api/v1/auth/register", json=register_payload())

    assert "impulso_session" in auth_client.cookies
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "httponly" in set_cookie_header.lower()
    assert auth_client.cookies["impulso_session"] not in response.text


def test_register_creates_exactly_one_row_in_each_table(auth_client: TestClient, db_session) -> None:
    before = {
        model: _count(db_session, model) for model in (User, Organization, Workspace, Membership, AuthSession)
    }

    auth_client.post("/api/v1/auth/register", json=register_payload())

    for model, before_count in before.items():
        after_count = _count(db_session, model)
        assert after_count == before_count + 1, f"{model.__tablename__} did not gain exactly one row"


def test_duplicate_email_registration_is_rejected(auth_client: TestClient) -> None:
    payload = register_payload()
    first = auth_client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201

    second = auth_client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


def test_duplicate_email_different_casing_is_still_rejected(auth_client: TestClient) -> None:
    payload = register_payload(email="Someone@Example.COM")
    first = auth_client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201

    second = auth_client.post("/api/v1/auth/register", json={**payload, "email": "someone@example.com"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


def test_registration_failure_mid_transaction_leaves_no_orphan_rows(db_session) -> None:
    """Directly exercises AuthService.register() so a forced failure
    inside the membership step never reaches session.commit() — proving
    the whole operation is atomic without going through HTTP."""
    from app.auth.service import AuthService

    before = {model: _count(db_session, model) for model in (User, Organization, Workspace, Membership)}

    service = AuthService(db_session)
    with patch(
        "app.workspaces.repository.MembershipRepository.create",
        side_effect=RuntimeError("simulated failure before commit"),
    ):
        with pytest.raises(RuntimeError, match="simulated failure before commit"):
            service.register(
                email="atomic-test@example.com",
                password="correct horse battery staple",
                display_name="Atomic Test",
                organization_name=None,
                workspace_name=None,
                user_agent=None,
            )

    # AuthService never called commit() before the forced failure, so a
    # rollback on this same session discards every add()/flush() from the
    # attempt — exactly what `get_db` would do for a real failed request.
    db_session.rollback()

    for model, before_count in before.items():
        after_count = _count(db_session, model)
        assert after_count == before_count, f"{model.__tablename__} has an orphan row after a failed registration"
