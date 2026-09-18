"""API contract, authorization, tenancy, and non-leakage tests for Governed
Experiment creation (MVP-32B, implementing the frozen MVP-32A/-32A-R1
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


def _hypotheses_path(fixtures: dict, strategy_public_id: str) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy/{strategy_public_id}/hypotheses"


def _experiments_path(fixtures: dict, hypothesis_public_id: str) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/hypotheses/{hypothesis_public_id}/experiments"


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
    response = _post(fixtures, _hypotheses_path(fixtures, strategy_id), {"statement": "x"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- happy path: any active membership (MEMBER+) can create ------------------


def test_owner_can_create_an_experiment(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)

    response = _post(
        fixtures, _experiments_path(fixtures, hypothesis_id),
        {"description": "A/B test two onboarding email sequences against a held-out control group."},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"].startswith("EXP-")
    assert body["hypothesis_id"] == hypothesis_id
    assert body["description"] == "A/B test two onboarding email sequences against a held-out control group."
    assert body["status"] == "RECORDED"


def test_member_can_create_an_experiment(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}

    response = _post(member_fixtures, _experiments_path(member_fixtures, hypothesis_id), {"description": "x"})
    assert response.status_code == 201, response.text


def test_admin_can_create_an_experiment(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    admin = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.ADMIN)
    admin_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(admin_client, email=admin["email"], password=admin["password"])
    admin_fixtures = {**fixtures, "client": admin_client, "csrf_token": token}

    response = _post(admin_fixtures, _experiments_path(admin_fixtures, hypothesis_id), {"description": "x"})
    assert response.status_code == 201, response.text


# --- CSRF ----------------------------------------------------------------------


def test_create_requires_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    response = fixtures["client"].post(_experiments_path(fixtures, hypothesis_id), json={"description": "x"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_INVALID"


# --- authentication --------------------------------------------------------------


def test_create_requires_authentication(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    anonymous_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    response = anonymous_client.post(_experiments_path(fixtures, hypothesis_id), json={"description": "x"})
    assert response.status_code == 401


# --- malformed request ---------------------------------------------------------


def test_create_rejects_empty_description(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    response = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": ""})
    assert response.status_code == 422


def test_create_rejects_blank_after_trim_description(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    response = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "   "})
    assert response.status_code == 422


def test_create_trims_the_description(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    response = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "  padded  "})
    assert response.status_code == 201
    assert response.json()["description"] == "padded"


def test_create_rejects_overlength_description(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    response = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "x" * 4001})
    assert response.status_code == 422


def test_create_rejects_unknown_extra_field(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    response = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "x", "status": "CONFIRMED"})
    assert response.status_code == 422


def test_create_rejects_client_supplied_hypothesis_id_field(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    response = _post(
        fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "x", "hypothesis_id": "HYP-FAKE"}
    )
    assert response.status_code == 422


def test_create_rejects_client_supplied_strategy_id_field(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    response = _post(
        fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "x", "strategy_id": "STR-FAKE"}
    )
    assert response.status_code == 422


# --- domain gate surfaced over HTTP --------------------------------------------


def test_create_against_an_unknown_hypothesis_is_a_403(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = _post(fixtures, _experiments_path(fixtures, "HYP-TOTALLYFAKE0"), {"description": "x"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_create_against_a_hypothesis_with_a_historical_strategy_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)

    approval_id = _record_approved_approval(fixtures)
    revised = _post(
        fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy/{strategy_id}/revision",
        {"strategic_approval_id": approval_id, "summary": "x", "positioning_statement": "y"},
    )
    assert revised.status_code == 201

    stale = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "attempted against historical strategy"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "EXPERIMENT_STRATEGY_STALE"

    # A hypothesis under the NEW current strategy remains fully eligible.
    new_strategy_id = revised.json()["strategy"]["id"]
    new_hypothesis_id = _build_hypothesis(fixtures, new_strategy_id)
    ok = _post(fixtures, _experiments_path(fixtures, new_hypothesis_id), {"description": "attempted against v2"})
    assert ok.status_code == 201


# --- no generic mutation surface ------------------------------------------------


def test_no_patch_or_delete_route_exists(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    assert fixtures["client"].patch(_experiments_path(fixtures, hypothesis_id), json={}, headers=headers).status_code == 405
    assert fixtures["client"].delete(_experiments_path(fixtures, hypothesis_id), headers=headers).status_code == 405


# --- readback: existing GET /strategy exposes the new Experiment ---------------


def test_created_experiment_is_visible_via_get_strategy(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    strategy_id = _build_base_strategy(fixtures["campaign_id"])
    hypothesis_id = _build_hypothesis(fixtures, strategy_id)
    created = _post(fixtures, _experiments_path(fixtures, hypothesis_id), {"description": "Readback test."})
    assert created.status_code == 201

    current = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()
    matching = [e for e in current["experiments"] if e["id"] == created.json()["id"]]
    assert len(matching) == 1
    assert matching[0]["hypothesis_id"] == hypothesis_id
    assert matching[0]["description"] == "Readback test."
    assert matching[0]["status"] == "RECORDED"


# --- tenancy: non-leaky campaign-scoped resolution --------------------------


def test_tenant_a_cannot_create_an_experiment_under_tenant_bs_hypothesis(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    campaign_b_id = body_b["campaign"]["id"]
    strategy_b_id = _build_base_strategy(campaign_b_id)
    fixtures_b = {"client": client_b, "csrf_token": csrf_b, "campaign_id": campaign_b_id}
    hypothesis_b_id = _build_hypothesis(fixtures_b, strategy_b_id)

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()
    campaign_a_id = body_a["campaign"]["id"]

    cross_tenant_response = client_a.post(
        f"/api/v1/campaigns/{campaign_a_id}/hypotheses/{hypothesis_b_id}/experiments",
        json={"description": "x"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert cross_tenant_response.status_code == 403 and cross_tenant_response.json()["error"]["code"] == "FORBIDDEN"

    # No mutation: B's own current Strategy still has zero Experiments.
    still_current = client_b.get(f"/api/v1/campaigns/{campaign_b_id}/strategy").json()
    assert still_current["experiments"] == []


# --- shared helper (mirrors tests/test_strategy_revision_api.py) -------------


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
