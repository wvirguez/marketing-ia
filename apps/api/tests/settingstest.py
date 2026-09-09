"""Shared helpers/fixtures for Settings tests (BACKEND-12) — real
database required.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as OrmSession

from app.auth.security import hash_password
from app.persistence.session import get_engine
from app.users.repository import UserRepository
from app.users.models import normalize_email
from app.workspaces.models import MembershipRole
from app.workspaces.repository import MembershipRepository, WorkspaceRepository
from tests.authtest import unique_email
from tests.campaignstest import register_and_get_csrf


@pytest.fixture()
def settings_client(auth_client: TestClient) -> Iterator[dict]:
    """A freshly-registered user (OWNER of their own, freshly-created
    workspace) — everything a Settings test needs:
    ``{"client", "csrf_token", "workspace_id", "user_id"}`` (both ids are
    public ids)."""
    csrf_token = register_and_get_csrf(auth_client)
    workspace = auth_client.get("/api/v1/workspaces/current").json()
    me = auth_client.get("/api/v1/users/me").json()
    yield {
        "client": auth_client,
        "csrf_token": csrf_token,
        "workspace_id": workspace["id"],
        "user_id": me["id"],
    }


def add_member_to_workspace(*, workspace_public_id: str, role: MembershipRole) -> dict:
    """Adds a brand-new user to an existing workspace with the given
    role, via a genuinely separate, immediately-committed session bound
    to the app's own engine — there is no invite endpoint in this API,
    so this mirrors ``tests/test_content_api.py``'s own
    ``_record_content_piece`` helper shape for setup that has no public
    write path. Returns ``{"email", "password", "display_name"}`` so the
    caller can log in as this user through the real HTTP client."""
    email = unique_email("member")
    password = "correct horse battery staple"
    display_name = f"Member {role.value}"

    engine = get_engine()
    with OrmSession(bind=engine) as session:
        workspace = WorkspaceRepository(session).get_by_public_id(workspace_public_id)
        assert workspace is not None
        user = UserRepository(session).create(
            email=email,
            normalized_email=normalize_email(email),
            password_hash=hash_password(password),
            display_name=display_name,
        )
        MembershipRepository(session).create(user_id=user.id, workspace_id=workspace.id, role=role)
        session.commit()

    return {"email": email, "password": password, "display_name": display_name}


def login_as(client: TestClient, *, email: str, password: str) -> str:
    """Logs the given (already-provisioned) user in on ``client`` and
    returns a fresh CSRF token for them."""
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return client.get("/api/v1/auth/csrf").json()["csrf_token"]
