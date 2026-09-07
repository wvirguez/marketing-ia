"""Multi-tenant safety (BACKEND-04 §22/§34): User A cannot access
Workspace B merely by knowing its public ID; Membership is mandatory.

Real database required — this proves the actual SQL-backed authorization
check, not a mock.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.api_errors import ForbiddenError
from app.users.models import User
from app.workspaces.service import WorkspaceAccessService
from tests.authtest import register_payload

pytestmark = pytest.mark.postgres


@pytest.fixture()
def two_users_two_workspaces(auth_client: TestClient) -> dict:
    """Registers two independent users (each bootstrapping their own
    Organization + Workspace + OWNER Membership, per BACKEND-04 §6/§35)
    and returns everything needed to test the full access matrix."""
    from fastapi.testclient import TestClient as _TestClient

    client_a = auth_client
    body_a = client_a.post("/api/v1/auth/register", json=register_payload(display_name="User A")).json()

    client_b = _TestClient(auth_client.app, raise_server_exceptions=False)
    body_b = client_b.post("/api/v1/auth/register", json=register_payload(display_name="User B")).json()

    return {
        "client_a": client_a,
        "client_b": client_b,
        "workspace_a_id": body_a["workspace"]["id"],
        "workspace_b_id": body_b["workspace"]["id"],
    }


def test_a_can_access_a(two_users_two_workspaces: dict) -> None:
    response = two_users_two_workspaces["client_a"].get("/api/v1/workspaces/current")
    assert response.status_code == 200
    assert response.json()["id"] == two_users_two_workspaces["workspace_a_id"]


def test_b_can_access_b(two_users_two_workspaces: dict) -> None:
    response = two_users_two_workspaces["client_b"].get("/api/v1/workspaces/current")
    assert response.status_code == 200
    assert response.json()["id"] == two_users_two_workspaces["workspace_b_id"]


def test_a_denied_workspace_b_by_service_layer(two_users_two_workspaces: dict, db_session) -> None:
    """`get_current_workspace` never accepts a client-supplied workspace
    id at all (see app/auth/dependencies.py) — there is structurally no
    endpoint through which A could even attempt to pass B's id. This
    proves the underlying authorization primitive every future by-id
    endpoint must use (`WorkspaceAccessService`) correctly denies it."""
    fixtures = two_users_two_workspaces
    user_a_id = _current_user_internal_id(fixtures["client_a"], db_session)

    with pytest.raises(ForbiddenError):
        WorkspaceAccessService(db_session).get_authorized_workspace(
            user_id=user_a_id, workspace_public_id=fixtures["workspace_b_id"]
        )


def test_b_denied_workspace_a_by_service_layer(two_users_two_workspaces: dict, db_session) -> None:
    fixtures = two_users_two_workspaces
    user_b_id = _current_user_internal_id(fixtures["client_b"], db_session)

    with pytest.raises(ForbiddenError):
        WorkspaceAccessService(db_session).get_authorized_workspace(
            user_id=user_b_id, workspace_public_id=fixtures["workspace_a_id"]
        )


def test_a_allowed_workspace_a_by_service_layer(two_users_two_workspaces: dict, db_session) -> None:
    fixtures = two_users_two_workspaces
    user_a_id = _current_user_internal_id(fixtures["client_a"], db_session)

    workspace = WorkspaceAccessService(db_session).get_authorized_workspace(
        user_id=user_a_id, workspace_public_id=fixtures["workspace_a_id"]
    )
    assert workspace.public_id == fixtures["workspace_a_id"]


def test_b_allowed_workspace_b_by_service_layer(two_users_two_workspaces: dict, db_session) -> None:
    fixtures = two_users_two_workspaces
    user_b_id = _current_user_internal_id(fixtures["client_b"], db_session)

    workspace = WorkspaceAccessService(db_session).get_authorized_workspace(
        user_id=user_b_id, workspace_public_id=fixtures["workspace_b_id"]
    )
    assert workspace.public_id == fixtures["workspace_b_id"]


def test_unknown_workspace_public_id_is_indistinguishable_from_not_a_member(db_session, two_users_two_workspaces) -> None:
    user_a_id = _current_user_internal_id(two_users_two_workspaces["client_a"], db_session)

    with pytest.raises(ForbiddenError):
        WorkspaceAccessService(db_session).get_authorized_workspace(
            user_id=user_a_id, workspace_public_id="WKS-DOESNOTEXIST"
        )


def _current_user_internal_id(client: TestClient, db_session):
    """Test-only helper: resolves the internal UUID for whichever user
    `client`'s cookie currently authenticates as, via the real `/session`
    endpoint (public id) + a direct lookup (internal id) — exactly the
    boundary BACKEND-01 draws between the two ID systems."""
    public_id = client.get("/api/v1/auth/session").json()["user"]["id"]
    user = db_session.execute(select(User).where(User.public_id == public_id)).scalar_one()
    return user.id
