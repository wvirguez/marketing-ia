"""API contract, authorization, tenancy, and non-leakage tests for
StrategicApproval (MVP-29B, implementing the frozen MVP-29A contract).
All marked `postgres`.

Reuses ``tests/test_strategic_decision_api.py``'s own ancestry-building
helpers (a StrategicApproval always needs a real, currently-ADOPT
StrategicDecision resting on a full Learning/Implication ancestry)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.workspaces.models import MembershipRole
from tests.campaignstest import campaign_payload
from tests.settingstest import add_member_to_workspace, login_as
from tests.test_strategic_decision_api import _build_accepted_recommendation

pytestmark = pytest.mark.postgres


def _decisions_path(fixtures: dict, suffix: str = "") -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/strategic-decisions{suffix}"


def _approvals_path(fixtures: dict, suffix: str = "") -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/strategic-approvals{suffix}"


def _approval_path(fixtures: dict, decision_id: str) -> str:
    return _decisions_path(fixtures, f"/{decision_id}/approval")


def _post(fixtures: dict, path: str, json: dict) -> object:
    return fixtures["client"].post(path, json=json, headers={"X-CSRF-Token": fixtures["csrf_token"]})


def _record_adopt_decision(fixtures: dict) -> str:
    """Builds a full, real, currently-ADOPT StrategicDecision via HTTP and
    returns its public_id — the sole implemented Approval-eligible input
    (MVP-29A §X)."""
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    response = _post(
        fixtures, _decisions_path(fixtures),
        {"strategic_recommendation_candidate_id": recommendation_id, "decision_type": "ADOPT", "statement": "Adopt this direction."},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _record_decision(fixtures: dict, decision_type: str) -> str:
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    response = _post(
        fixtures, _decisions_path(fixtures),
        {"strategic_recommendation_candidate_id": recommendation_id, "decision_type": decision_type, "statement": "x"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- happy path: OWNER can record an approval --------------------------------


def test_owner_can_record_an_approval(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_adopt_decision(fixtures)

    response = _post(fixtures, _approval_path(fixtures, decision_id), {"outcome": "APPROVED"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["outcome"] == "APPROVED"
    assert body["strategic_decision_id"] == decision_id

    read = fixtures["client"].get(_approval_path(fixtures, decision_id))
    assert read.status_code == 200
    assert read.json()["id"] == body["id"]

    listing = fixtures["client"].get(_approvals_path(fixtures)).json()
    assert {item["id"] for item in listing["items"]} == {body["id"]}


def test_owner_can_record_a_rejected_approval(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_adopt_decision(fixtures)
    response = _post(fixtures, _approval_path(fixtures, decision_id), {"outcome": "REJECTED"})
    assert response.status_code == 201
    assert response.json()["outcome"] == "REJECTED"


def test_admin_can_record_an_approval(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_adopt_decision(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    admin = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.ADMIN)
    admin_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(admin_client, email=admin["email"], password=admin["password"])
    admin_fixtures = {**fixtures, "client": admin_client, "csrf_token": token}

    response = _post(admin_fixtures, _approval_path(admin_fixtures, decision_id), {"outcome": "APPROVED"})
    assert response.status_code == 201 and response.json()["outcome"] == "APPROVED"


# --- read semantics: no approval yet, distinctly shown -----------------------


def test_get_approval_for_decision_without_one_returns_null(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_adopt_decision(fixtures)
    response = fixtures["client"].get(_approval_path(fixtures, decision_id))
    assert response.status_code == 200
    assert response.json() is None


# --- application authority: MEMBER cannot write -------------------------------


def test_member_cannot_record_an_approval(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_adopt_decision(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}

    response = _post(member_fixtures, _approval_path(member_fixtures, decision_id), {"outcome": "APPROVED"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"

    # Member can still read.
    read = member_client.get(_approval_path(fixtures, decision_id))
    assert read.status_code == 200 and read.json() is None

    # No mutation occurred: OWNER can still record it afterward.
    owner_response = _post(fixtures, _approval_path(fixtures, decision_id), {"outcome": "APPROVED"})
    assert owner_response.status_code == 201


# --- CSRF ----------------------------------------------------------------------


def test_record_requires_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_adopt_decision(fixtures)
    response = fixtures["client"].post(_approval_path(fixtures, decision_id), json={"outcome": "APPROVED"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_INVALID"


# --- malformed request ---------------------------------------------------------


def test_record_rejects_unknown_outcome(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_adopt_decision(fixtures)
    response = _post(fixtures, _approval_path(fixtures, decision_id), {"outcome": "PENDING"})
    assert response.status_code == 422


def test_record_rejects_unknown_extra_field(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_adopt_decision(fixtures)
    response = _post(fixtures, _approval_path(fixtures, decision_id), {"outcome": "APPROVED", "rationale": "x"})
    assert response.status_code == 422


# --- domain gate surfaced over HTTP --------------------------------------------


def test_record_against_a_defer_decision_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_decision(fixtures, "DEFER")
    response = _post(fixtures, _approval_path(fixtures, decision_id), {"outcome": "APPROVED"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "STRATEGIC_DECISION_NOT_ELIGIBLE_FOR_APPROVAL"


def test_record_against_a_decline_decision_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_decision(fixtures, "DECLINE")
    response = _post(fixtures, _approval_path(fixtures, decision_id), {"outcome": "REJECTED"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "STRATEGIC_DECISION_NOT_ELIGIBLE_FOR_APPROVAL"


def test_record_against_an_already_superseded_decision_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_adopt_decision(fixtures)
    superseded = _post(fixtures, _decisions_path(fixtures, f"/{decision_id}/supersede"), {"decision_type": "DEFER", "statement": "x"})
    assert superseded.status_code == 201

    response = _post(fixtures, _approval_path(fixtures, decision_id), {"outcome": "APPROVED"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "STRATEGIC_DECISION_NOT_ELIGIBLE_FOR_APPROVAL"


def test_second_approval_attempt_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_adopt_decision(fixtures)
    first = _post(fixtures, _approval_path(fixtures, decision_id), {"outcome": "APPROVED"})
    assert first.status_code == 201
    second = _post(fixtures, _approval_path(fixtures, decision_id), {"outcome": "REJECTED"})
    assert second.status_code == 409 and second.json()["error"]["code"] == "STRATEGIC_APPROVAL_ALREADY_EXISTS"


# --- Decision supersession interaction over HTTP (MVP-29A §L) ---------------


def test_approval_remains_readable_after_its_decision_is_superseded(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_adopt_decision(fixtures)
    approval = _post(fixtures, _approval_path(fixtures, decision_id), {"outcome": "APPROVED"}).json()

    superseded = _post(fixtures, _decisions_path(fixtures, f"/{decision_id}/supersede"), {"decision_type": "DEFER", "statement": "x"})
    assert superseded.status_code == 201
    replacement_id = superseded.json()["id"]

    still_readable = fixtures["client"].get(_approval_path(fixtures, decision_id))
    assert still_readable.status_code == 200
    assert still_readable.json()["id"] == approval["id"]

    replacement_approval = fixtures["client"].get(_approval_path(fixtures, replacement_id))
    assert replacement_approval.status_code == 200
    assert replacement_approval.json() is None  # never inherited (MVP-29A §O)


# --- no generic mutation surface ----------------------------------------------


def test_no_patch_or_delete_route_exists(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    decision_id = _record_adopt_decision(fixtures)
    _post(fixtures, _approval_path(fixtures, decision_id), {"outcome": "APPROVED"})
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    assert fixtures["client"].patch(_approval_path(fixtures, decision_id), json={"outcome": "REJECTED"}, headers=headers).status_code == 405
    assert fixtures["client"].delete(_approval_path(fixtures, decision_id), headers=headers).status_code == 405


# --- tenancy: non-leaky campaign-scoped resolution ---------------------------


def test_unknown_decision_approval_read_is_forbidden_non_leakily(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].get(_approval_path(fixtures, "DEC-TOTALLYFAKE0"))
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_tenant_a_cannot_read_or_approve_tenant_bs_decision(auth_client: TestClient) -> None:
    client_a = auth_client
    from tests.campaignstest import register_and_get_csrf

    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    campaign_b_id = body_b["campaign"]["id"]
    fixtures_b = {"client": client_b, "csrf_token": csrf_b, "campaign_id": campaign_b_id}
    decision_b_id = _record_adopt_decision(fixtures_b)

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()
    campaign_a_id = body_a["campaign"]["id"]

    read_response = client_a.get(f"/api/v1/campaigns/{campaign_a_id}/strategic-decisions/{decision_b_id}/approval")
    assert read_response.status_code == 403 and read_response.json()["error"]["code"] == "FORBIDDEN"

    approve_response = client_a.post(
        f"/api/v1/campaigns/{campaign_a_id}/strategic-decisions/{decision_b_id}/approval",
        json={"outcome": "APPROVED"}, headers={"X-CSRF-Token": csrf_a},
    )
    assert approve_response.status_code == 403 and approve_response.json()["error"]["code"] == "FORBIDDEN"

    # No mutation: B's own decision still has no approval, recordable normally.
    still_none = client_b.get(f"/api/v1/campaigns/{campaign_b_id}/strategic-decisions/{decision_b_id}/approval")
    assert still_none.status_code == 200 and still_none.json() is None
