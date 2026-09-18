"""API contract, authorization, tenancy, and non-leakage tests for Governed
Content Plan creation (MVP-33B, implementing the frozen MVP-33A/-33A-R1
contract). All marked `postgres`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as OrmSession

from app.campaigns.repository import CampaignRepository
from app.persistence.session import get_engine
from app.workspaces.models import MembershipRole
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.orchestrationtest import build_base_strategy
from tests.settingstest import add_member_to_workspace, login_as

pytestmark = pytest.mark.postgres


def _plan_path(fixtures: dict) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/plan"


def _post(fixtures: dict, path: str, json: dict) -> object:
    return fixtures["client"].post(path, json=json, headers={"X-CSRF-Token": fixtures["csrf_token"]})


def _build_base_strategy(campaign_public_id: str) -> str:
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_public_id)
        base_strategy, _run, _stage = build_base_strategy(session, campaign=campaign)
        session.commit()
        return base_strategy.public_id


def _build_hypothesis(fixtures: dict, strategy_id: str) -> str:
    response = _post(
        fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy/{strategy_id}/hypotheses", {"statement": "x"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _build_experiment(fixtures: dict, hypothesis_id: str) -> str:
    response = _post(
        fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/hypotheses/{hypothesis_id}/experiments",
        {"description": "x"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _build_experiment_chain(fixtures: dict) -> str:
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    return _build_experiment(fixtures, hypothesis_id)


# --- happy path: any active membership (MEMBER+) can create ------------------


def test_owner_can_create_a_generic_plan(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _post(fixtures, _plan_path(fixtures), {"summary": "A generic content plan."})
    assert response.status_code == 201, response.text
    body = response.json()["plan"]
    assert body["id"].startswith("PLN-")
    assert body["experiment_id"] is None
    assert body["summary"] == "A generic content plan."


def test_member_can_create_a_plan(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}

    response = _post(member_fixtures, _plan_path(member_fixtures), {"summary": "x"})
    assert response.status_code == 201, response.text


def test_admin_can_create_a_plan(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    admin = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.ADMIN)
    admin_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(admin_client, email=admin["email"], password=admin["password"])
    admin_fixtures = {**fixtures, "client": admin_client, "csrf_token": token}

    response = _post(admin_fixtures, _plan_path(admin_fixtures), {"summary": "x"})
    assert response.status_code == 201, response.text


# --- CSRF / authentication ----------------------------------------------------


def test_create_requires_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(_plan_path(fixtures), json={"summary": "x"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_INVALID"


def test_create_requires_authentication(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    anonymous_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    response = anonymous_client.post(_plan_path(fixtures), json={"summary": "x"})
    assert response.status_code == 401


# --- malformed request ---------------------------------------------------------


def test_create_rejects_empty_summary(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _post(fixtures, _plan_path(fixtures), {"summary": ""})
    assert response.status_code == 422


def test_create_rejects_blank_after_trim_summary(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _post(fixtures, _plan_path(fixtures), {"summary": "   "})
    assert response.status_code == 422


def test_create_rejects_unknown_extra_field(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _post(fixtures, _plan_path(fixtures), {"summary": "x", "origin": "GOVERNED"})
    assert response.status_code == 422


def test_create_rejects_client_supplied_campaign_run_id(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _post(fixtures, _plan_path(fixtures), {"summary": "x", "campaign_run_id": "RUN-FAKE"})
    assert response.status_code == 422


def test_create_rejects_client_supplied_version(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _post(fixtures, _plan_path(fixtures), {"summary": "x", "version": 99})
    assert response.status_code == 422


# --- Case E: Experiment-derived plan --------------------------------------------


def test_create_experiment_derived_plan(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id = _build_experiment_chain(fixtures)
    response = _post(
        fixtures, _plan_path(fixtures), {"summary": "Operationalizes the experiment.", "experiment_public_id": experiment_id}
    )
    assert response.status_code == 201, response.text
    assert response.json()["plan"]["experiment_id"] == experiment_id


def test_create_rejects_unknown_experiment_public_id(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _post(fixtures, _plan_path(fixtures), {"summary": "x", "experiment_public_id": "EXP-TOTALLYFAKE0"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_historical_strategy_experiment_still_produces_a_valid_plan(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    experiment_id = _build_experiment(fixtures, hypothesis_id)

    approval_id = _record_approved_approval(fixtures)
    revised = _post(
        fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy/{strategy_id}/revision",
        {"strategic_approval_id": approval_id, "summary": "x", "positioning_statement": "y"},
    )
    assert revised.status_code == 201, revised.text

    response = _post(
        fixtures, _plan_path(fixtures),
        {"summary": "Plans content for a now-historical experiment.", "experiment_public_id": experiment_id},
    )
    assert response.status_code == 201, response.text
    assert response.json()["plan"]["experiment_id"] == experiment_id


# --- items ------------------------------------------------------------------------


def test_zero_items_accepted(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _post(fixtures, _plan_path(fixtures), {"summary": "x", "items": []})
    assert response.status_code == 201, response.text
    assert response.json()["items"] == []


def test_valid_items_accepted(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _post(
        fixtures, _plan_path(fixtures),
        {"summary": "x", "items": [{"format": "Reel", "objective": "Introduce.", "sequence": 1, "scheduled_date": None}]},
    )
    assert response.status_code == 201, response.text
    assert len(response.json()["items"]) == 1


# --- no generic mutation surface ------------------------------------------------


def test_no_patch_or_delete_route_exists(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    assert fixtures["client"].patch(_plan_path(fixtures), json={}, headers=headers).status_code == 405
    assert fixtures["client"].delete(_plan_path(fixtures), headers=headers).status_code == 405


# --- readback: existing GET /plan exposes the new Plan --------------------------


def test_created_plan_is_visible_via_get_plan(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    created = _post(fixtures, _plan_path(fixtures), {"summary": "Readback test."})
    assert created.status_code == 201

    current = fixtures["client"].get(_plan_path(fixtures)).json()
    assert current["plan"]["id"] == created.json()["plan"]["id"]
    assert current["plan"]["summary"] == "Readback test."
    assert current["plan"]["experiment_id"] is None


# --- tenancy: non-leaky campaign-scoped resolution --------------------------


def test_tenant_a_cannot_reference_tenant_bs_experiment(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    campaign_b_id = body_b["campaign"]["id"]
    fixtures_b = {"client": client_b, "csrf_token": csrf_b, "campaign_id": campaign_b_id}
    experiment_b_id = _build_experiment_chain(fixtures_b)

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()
    campaign_a_id = body_a["campaign"]["id"]

    cross_tenant_response = client_a.post(
        f"/api/v1/campaigns/{campaign_a_id}/plan",
        json={"summary": "x", "experiment_public_id": experiment_b_id},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert cross_tenant_response.status_code == 403 and cross_tenant_response.json()["error"]["code"] == "FORBIDDEN"

    # No mutation: A's own plan surface remains empty.
    still_empty = client_a.get(f"/api/v1/campaigns/{campaign_a_id}/plan", headers={"X-CSRF-Token": csrf_a}).json()
    assert still_empty["plan"] is None


# --- shared helper (mirrors tests/test_experiment_api.py) -------------------


def _record_approved_approval(fixtures: dict) -> str:
    from tests.test_strategic_decision_api import _build_accepted_recommendation

    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    decision_response = _post(
        fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/strategic-decisions",
        {"strategic_recommendation_candidate_id": recommendation_id, "decision_type": "ADOPT", "statement": "Adopt this direction."},
    )
    assert decision_response.status_code == 201, decision_response.text
    decision_id = decision_response.json()["id"]
    approval_response = _post(
        fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/strategic-decisions/{decision_id}/approval",
        {"outcome": "APPROVED"},
    )
    assert approval_response.status_code == 201, approval_response.text
    return approval_response.json()["id"]
