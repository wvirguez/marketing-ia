"""Distribution HTTP contract and human attestation; real PostgreSQL only."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.models import ContentApproval, ContentDistribution, ContentVersion
from app.persistence.session import get_engine
from app.workspaces.models import Membership, MembershipRole, MembershipStatus
from app.users.models import User
from tests.settingstest import add_member_to_workspace, login_as
from tests.campaignstest import register_and_get_csrf
from tests.test_content_api import (
    _advance_to_ready_for_review, _lifecycle_path, _post, _record_content_piece,
    _under_review_approval_id, campaign_client_with_stages,
)

pytestmark = pytest.mark.postgres


def _approved(fixtures):
    content_id = _record_content_piece(fixtures)
    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)
    response = _post(fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "APPROVED"})
    assert response.status_code == 200, response.text
    return content_id


def _path(fixtures, content_id, action):
    return _lifecycle_path(fixtures, content_id, f"/distribution/{action}")


def test_distribution_happy_path_freezes_approval_version_and_channel(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _approved(fixtures)
    ready = fixtures["client"].post(_path(fixtures, content_id, "mark-ready-for-distribution"), headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert ready.status_code == 201, ready.text
    body = ready.json()
    assert body["piece"]["status"] == "READY_FOR_DISTRIBUTION"
    assert body["distribution"]["status"] == "READY"
    assert body["distribution"]["id"].startswith("DST-")
    assert body["distribution"]["channel"] == body["piece"]["channel"]
    assert body["distribution"]["external_reference"] is None
    assert body["distribution"]["distributed_at"] is None
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", ready.text)
    assert fixtures["client"].post(_path(fixtures, content_id, "mark-ready-for-distribution"), headers={"X-CSRF-Token": fixtures["csrf_token"]}).status_code == 409

    with Session(get_engine()) as session:
        distribution = session.execute(select(ContentDistribution).where(ContentDistribution.public_id == body["distribution"]["id"])).scalar_one()
        approval = session.execute(select(ContentApproval).join(ContentVersion, ContentApproval.content_version_id == ContentVersion.id).where(ContentVersion.content_piece_id == distribution.content_piece_id)).scalar_one()
        assert distribution.content_version_id == approval.content_version_id

    recorded = fixtures["client"].post(_path(fixtures, content_id, "record-distributed"), json={"external_reference": "opaque:abc"}, headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["piece"]["status"] == "DISTRIBUTED"
    assert recorded.json()["distribution"]["status"] == "DISTRIBUTED"
    assert recorded.json()["distribution"]["external_reference"] == "opaque:abc"
    assert recorded.json()["distribution"]["distributed_at"] is not None
    assert fixtures["client"].post(_path(fixtures, content_id, "record-distributed"), json={}, headers={"X-CSRF-Token": fixtures["csrf_token"]}).status_code == 409


def test_distribution_requires_lifecycle_csrf_and_valid_attestation(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)
    ready_path = _path(fixtures, content_id, "mark-ready-for-distribution")
    record_path = _path(fixtures, content_id, "record-distributed")
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    assert fixtures["client"].post(ready_path, headers=headers).status_code == 409
    assert fixtures["client"].post(ready_path).json()["error"]["code"] == "CSRF_INVALID"
    assert fixtures["client"].post(record_path, json={}).json()["error"]["code"] == "CSRF_INVALID"
    assert fixtures["client"].post(record_path, json={}, headers=headers).status_code == 409

    _advance_to_ready_for_review(fixtures, content_id)
    approval_id = _under_review_approval_id(fixtures, content_id)
    assert _post(fixtures, _lifecycle_path(fixtures, content_id, f"/approvals/{approval_id}/decision"), {"decision": "APPROVED"}).status_code == 200
    assert fixtures["client"].post(ready_path, headers=headers).status_code == 201
    assert fixtures["client"].post(record_path, json={"external_reference": "x" * 2049}, headers=headers).status_code == 422
    assert fixtures["client"].post(record_path, json={"unexpected": "x"}, headers=headers).status_code == 422
    assert fixtures["client"].post(record_path, json={"external_reference": "x" * 2048}, headers=headers).status_code == 200


def test_member_can_mark_ready_but_cannot_attest(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _approved(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    headers = {"X-CSRF-Token": token}
    assert member_client.post(_path(fixtures, content_id, "mark-ready-for-distribution"), headers=headers).status_code == 201
    response = member_client.post(_path(fixtures, content_id, "record-distributed"), json={}, headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_admin_can_record_and_missing_content_is_non_leaky(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _approved(fixtures)
    owner_headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    assert fixtures["client"].post(_path(fixtures, content_id, "mark-ready-for-distribution"), headers=owner_headers).status_code == 201
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    admin = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.ADMIN)
    admin_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(admin_client, email=admin["email"], password=admin["password"])
    headers = {"X-CSRF-Token": token}
    assert admin_client.post(_path(fixtures, content_id, "record-distributed"), json={}, headers=headers).status_code == 200
    for action in ("mark-ready-for-distribution", "record-distributed"):
        response = admin_client.post(_path(fixtures, "CNT-UNKNOWN", action), json={}, headers=headers)
        assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"
        wrong_campaign = {**fixtures, "campaign_id": "CAM-UNKNOWN"}
        response = admin_client.post(_path(wrong_campaign, content_id, action), json={}, headers=headers)
        assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_distribution_routes_require_authentication_and_active_membership(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _approved(fixtures)
    ready_path = _path(fixtures, content_id, "mark-ready-for-distribution")
    record_path = _path(fixtures, content_id, "record-distributed")
    anonymous_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    for path in (ready_path, record_path):
        response = anonymous_client.post(path, json={})
        assert response.status_code == 401 and response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED", response.text

    me = fixtures["client"].get("/api/v1/users/me").json()
    with Session(get_engine()) as session:
        user = session.execute(select(User).where(User.public_id == me["id"])).scalar_one()
        membership = session.execute(select(Membership).where(Membership.user_id == user.id)).scalar_one()
        membership.status = MembershipStatus.REVOKED
        session.commit()
    for path in (ready_path, record_path):
        response = fixtures["client"].post(path, json={}, headers={"X-CSRF-Token": fixtures["csrf_token"]})
        assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_other_workspace_cannot_mutate_distribution(campaign_client_with_stages):
    fixtures = campaign_client_with_stages
    content_id = _approved(fixtures)
    outsider = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = register_and_get_csrf(outsider, display_name="Other workspace")
    headers = {"X-CSRF-Token": token}
    for action in ("mark-ready-for-distribution", "record-distributed"):
        response = outsider.post(_path(fixtures, content_id, action), json={}, headers=headers)
        assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"
