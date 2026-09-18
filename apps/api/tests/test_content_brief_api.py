"""API contract, authorization, tenancy, and non-leakage tests for Governed
Content Brief creation (MVP-34B, implementing the frozen MVP-34A contract).
All marked `postgres`.
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


def _brief_path(fixtures: dict, plan_item_id: str) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/plan/items/{plan_item_id}/brief"


def _post(fixtures: dict, path: str, json: dict) -> object:
    return fixtures["client"].post(path, json=json, headers={"X-CSRF-Token": fixtures["csrf_token"]})


def _create_plan_with_item(fixtures: dict, **plan_kwargs) -> tuple[str, str]:
    """Creates a governed Content Plan with exactly one Plan Item and
    returns ``(plan_id, plan_item_id)``."""
    payload = {
        "summary": "A two-week content calendar.",
        "items": [{"format": "Reel", "objective": "Introduce the offer.", "sequence": 1, "scheduled_date": None}],
    }
    payload.update(plan_kwargs)
    response = _post(fixtures, _plan_path(fixtures), payload)
    assert response.status_code == 201, response.text
    body = response.json()
    return body["plan"]["id"], body["items"][0]["id"]


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


def test_owner_can_create_a_brief(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)
    response = _post(fixtures, _brief_path(fixtures, item_id), {"brief": "Produce a beginner-friendly reel."})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"].startswith("CBRF-")
    assert body["plan_item_id"] == item_id
    assert body["brief"] == "Produce a beginner-friendly reel."


def test_member_can_create_a_brief(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}

    response = _post(member_fixtures, _brief_path(member_fixtures, item_id), {"brief": "x"})
    assert response.status_code == 201, response.text


def test_admin_can_create_a_brief(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    admin = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.ADMIN)
    admin_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(admin_client, email=admin["email"], password=admin["password"])
    admin_fixtures = {**fixtures, "client": admin_client, "csrf_token": token}

    response = _post(admin_fixtures, _brief_path(admin_fixtures, item_id), {"brief": "x"})
    assert response.status_code == 201, response.text


# --- CSRF / authentication ----------------------------------------------------


def test_create_requires_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)
    response = fixtures["client"].post(_brief_path(fixtures, item_id), json={"brief": "x"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_INVALID"


def test_create_requires_authentication(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)
    anonymous_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    response = anonymous_client.post(_brief_path(fixtures, item_id), json={"brief": "x"})
    assert response.status_code == 401


# --- malformed request ---------------------------------------------------------


def test_create_rejects_empty_brief(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)
    response = _post(fixtures, _brief_path(fixtures, item_id), {"brief": ""})
    assert response.status_code == 422


def test_create_rejects_blank_after_trim_brief(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)
    response = _post(fixtures, _brief_path(fixtures, item_id), {"brief": "   "})
    assert response.status_code == 422


def test_create_rejects_oversized_brief(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)
    response = _post(fixtures, _brief_path(fixtures, item_id), {"brief": "x" * 4001})
    assert response.status_code == 422


def test_create_rejects_unknown_extra_field(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)
    response = _post(fixtures, _brief_path(fixtures, item_id), {"brief": "x", "origin": "GOVERNED"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "forbidden_field", ["plan_item_id", "content_plan_id", "workspace_id", "campaign_id", "experiment_id", "actor_user_id"]
)
def test_create_rejects_client_supplied_server_derived_fields(campaign_run_client: dict, forbidden_field: str) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)
    response = _post(fixtures, _brief_path(fixtures, item_id), {"brief": "x", forbidden_field: "FAKE-1"})
    assert response.status_code == 422


# --- unknown / cross-tenant PlanItem ------------------------------------------


def test_create_rejects_unknown_plan_item(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _create_plan_with_item(fixtures)
    response = _post(fixtures, _brief_path(fixtures, "ITM-TOTALLYFAKE0"), {"brief": "x"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_same_workspace_different_campaign_plan_item_is_forbidden(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)

    csrf_token = fixtures["csrf_token"]
    other_campaign = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Other Campaign"), headers={"X-CSRF-Token": csrf_token}
    ).json()
    other_fixtures = {**fixtures, "campaign_id": other_campaign["campaign"]["id"]}

    response = _post(other_fixtures, _brief_path(other_fixtures, item_id), {"brief": "x"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_cross_workspace_plan_item_is_forbidden(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    csrf_b = register_and_get_csrf(client_b, display_name="User B")
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    fixtures_b = {"client": client_b, "csrf_token": csrf_b, "campaign_id": body_b["campaign"]["id"]}
    _plan_id, item_id = _create_plan_with_item(fixtures_b)

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()
    fixtures_a = {"client": client_a, "csrf_token": csrf_a, "campaign_id": body_a["campaign"]["id"]}

    response = _post(fixtures_a, _brief_path(fixtures_a, item_id), {"brief": "x"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


# --- duplicate create ----------------------------------------------------------


def test_duplicate_create_for_same_plan_item_is_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)

    first = _post(fixtures, _brief_path(fixtures, item_id), {"brief": "First attempt."})
    assert first.status_code == 201, first.text

    second = _post(fixtures, _brief_path(fixtures, item_id), {"brief": "Second attempt."})
    assert second.status_code == 409 and second.json()["error"]["code"] == "PLAN_ITEM_ALREADY_BRIEFED"


# --- no update/delete/re-brief surface -----------------------------------------


def test_no_patch_put_or_delete_route_exists(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)
    _post(fixtures, _brief_path(fixtures, item_id), {"brief": "x"})
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = _brief_path(fixtures, item_id)
    assert fixtures["client"].patch(path, json={}, headers=headers).status_code == 405
    assert fixtures["client"].put(path, json={}, headers=headers).status_code == 405
    assert fixtures["client"].delete(path, headers=headers).status_code == 405
    assert fixtures["client"].get(path, headers=headers).status_code == 405


# --- Case G / Case E -------------------------------------------------------------


def test_case_g_generic_plan_brief_makes_no_experiment_claim(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, item_id = _create_plan_with_item(fixtures)
    response = _post(fixtures, _brief_path(fixtures, item_id), {"brief": "x"})
    assert response.status_code == 201, response.text
    assert "experiment_id" not in response.json()


def test_case_e_experiment_derived_plan_brief_succeeds(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id = _build_experiment_chain(fixtures)
    _plan_id, item_id = _create_plan_with_item(
        fixtures, summary="Operationalizes the experiment.", experiment_public_id=experiment_id
    )
    response = _post(fixtures, _brief_path(fixtures, item_id), {"brief": "x"})
    assert response.status_code == 201, response.text


# --- historical PlanItem eligibility (MVP-34A §G, mandatory) -------------------


def test_historical_plan_item_brief_creation_succeeds(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_v1_id, item_v1_id = _create_plan_with_item(fixtures, summary="Version 1.")

    # A second Content Plan version supersedes v1 — v1's PlanItem must
    # remain eligible for governed Brief creation (MVP-34A §G: historical
    # eligibility is a deliberate contract decision, not an oversight).
    v2 = _post(fixtures, _plan_path(fixtures), {"summary": "Version 2.", "items": []})
    assert v2.status_code == 201 and v2.json()["plan"]["version"] == 2

    response = _post(fixtures, _brief_path(fixtures, item_v1_id), {"brief": "Briefing a historical item."})
    assert response.status_code == 201, response.text
    assert response.json()["plan_item_id"] == item_v1_id


# --- public schema: public IDs only ---------------------------------------------


def test_content_brief_public_uses_public_ids_only(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    plan_id, item_id = _create_plan_with_item(fixtures)
    response = _post(fixtures, _brief_path(fixtures, item_id), {"brief": "x"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"].startswith("CBRF-")
    assert body["plan_item_id"] == item_id
    assert body["content_plan_id"] == plan_id
    assert set(body.keys()) == {"id", "plan_item_id", "content_plan_id", "brief", "created_at"}


# --- readback: GET /plan exposes the created Brief -------------------------------


def test_get_plan_returns_null_for_unbriefed_item(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _create_plan_with_item(fixtures)
    body = fixtures["client"].get(_plan_path(fixtures)).json()
    assert body["items"][0]["brief"] is None


def test_get_plan_returns_the_created_brief(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    plan_id, item_id = _create_plan_with_item(fixtures)
    created = _post(fixtures, _brief_path(fixtures, item_id), {"brief": "Produce a beginner reel."})
    assert created.status_code == 201

    body = fixtures["client"].get(_plan_path(fixtures)).json()
    assert body["items"][0]["brief"] is not None
    assert body["items"][0]["brief"]["id"] == created.json()["id"]
    assert body["items"][0]["brief"]["brief"] == "Produce a beginner reel."
    assert body["items"][0]["brief"]["content_plan_id"] == plan_id
