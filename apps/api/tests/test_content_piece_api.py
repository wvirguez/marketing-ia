"""API contract, authorization, tenancy, and non-leakage tests for Governed
Content Piece creation (MVP-35B, implementing the frozen MVP-35A contract).
All marked `postgres`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
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


def _pieces_path(fixtures: dict, content_brief_id: str) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/content/briefs/{content_brief_id}/pieces"


def _post(fixtures: dict, path: str, json: dict) -> object:
    return fixtures["client"].post(path, json=json, headers={"X-CSRF-Token": fixtures["csrf_token"]})


def _piece_payload(**overrides: object) -> dict:
    payload = {
        "format": "Reel",
        "objective": "Generate identification and interest.",
        "funnel_stage": "Awareness",
        "cta": "Learn the method",
        "channel": "Instagram",
        "payload": {"kind": "draft", "hook": "Your dog isn't ignoring you."},
    }
    payload.update(overrides)
    return payload


def _create_brief(fixtures: dict, **plan_kwargs) -> tuple[str, str]:
    """Creates a governed ContentPlan + PlanItem + ContentBrief and
    returns ``(plan_id, content_brief_id)``."""
    plan_payload = {
        "summary": "A two-week content calendar.",
        "items": [{"format": "Reel", "objective": "Introduce the offer.", "sequence": 1, "scheduled_date": None}],
    }
    plan_payload.update(plan_kwargs)
    plan_response = _post(fixtures, _plan_path(fixtures), plan_payload)
    assert plan_response.status_code == 201, plan_response.text
    body = plan_response.json()
    plan_id = body["plan"]["id"]
    item_id = body["items"][0]["id"]

    brief_response = _post(fixtures, _brief_path(fixtures, item_id), {"brief": "Produce a beginner-friendly reel."})
    assert brief_response.status_code == 201, brief_response.text
    return plan_id, brief_response.json()["id"]


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


def test_owner_can_create_a_piece(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    response = _post(fixtures, _pieces_path(fixtures, brief_id), _piece_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["piece"]["id"].startswith("CNT-")
    assert body["piece"]["status"] == "DRAFT"
    assert body["piece"]["format"] == "Reel"
    assert body["latest_version"] is not None
    assert body["latest_version"]["payload"]["hook"] == "Your dog isn't ignoring you."
    assert body["latest_approval"] is None
    assert body["distribution"] is None


def test_member_can_create_a_piece(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}

    response = _post(member_fixtures, _pieces_path(member_fixtures, brief_id), _piece_payload())
    assert response.status_code == 201, response.text


def test_admin_can_create_a_piece(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    admin = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.ADMIN)
    admin_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(admin_client, email=admin["email"], password=admin["password"])
    admin_fixtures = {**fixtures, "client": admin_client, "csrf_token": token}

    response = _post(admin_fixtures, _pieces_path(admin_fixtures, brief_id), _piece_payload())
    assert response.status_code == 201, response.text


# --- CSRF / authentication ----------------------------------------------------


def test_create_requires_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    response = fixtures["client"].post(_pieces_path(fixtures, brief_id), json=_piece_payload())
    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_INVALID"


def test_create_requires_authentication(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    anonymous_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    response = anonymous_client.post(_pieces_path(fixtures, brief_id), json=_piece_payload())
    assert response.status_code == 401


# --- malformed request ---------------------------------------------------------


@pytest.mark.parametrize("field", ["format", "objective", "funnel_stage", "cta", "channel"])
def test_create_rejects_blank_required_field(campaign_run_client: dict, field: str) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    response = _post(fixtures, _pieces_path(fixtures, brief_id), _piece_payload(**{field: "   "}))
    assert response.status_code == 422


def test_create_rejects_missing_payload_field(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    body = _piece_payload()
    del body["payload"]
    response = _post(fixtures, _pieces_path(fixtures, brief_id), body)
    assert response.status_code == 422


def test_create_rejects_unknown_extra_field(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    response = _post(fixtures, _pieces_path(fixtures, brief_id), _piece_payload(status="APPROVED"))
    assert response.status_code == 422


@pytest.mark.parametrize(
    "forbidden_field", ["content_brief_id", "content_plan_id", "plan_item_id", "workspace_id", "campaign_id", "experiment_id", "variant_id", "actor_user_id", "origin", "version", "archived_at"]
)
def test_create_rejects_client_supplied_server_derived_fields(campaign_run_client: dict, forbidden_field: str) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    response = _post(fixtures, _pieces_path(fixtures, brief_id), _piece_payload(**{forbidden_field: "FAKE-1"}))
    assert response.status_code == 422


# --- unknown / cross-tenant ContentBrief ---------------------------------------


def test_create_rejects_unknown_brief(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _create_brief(fixtures)
    response = _post(fixtures, _pieces_path(fixtures, "CBRF-TOTALLYFAKE0"), _piece_payload())
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_same_workspace_different_campaign_brief_is_forbidden(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)

    csrf_token = fixtures["csrf_token"]
    other_campaign = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Other Campaign"), headers={"X-CSRF-Token": csrf_token}
    ).json()
    other_fixtures = {**fixtures, "campaign_id": other_campaign["campaign"]["id"]}

    response = _post(other_fixtures, _pieces_path(other_fixtures, brief_id), _piece_payload())
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_cross_workspace_brief_is_forbidden(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    csrf_b = register_and_get_csrf(client_b, display_name="User B")
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    fixtures_b = {"client": client_b, "csrf_token": csrf_b, "campaign_id": body_b["campaign"]["id"]}
    _plan_id, brief_id = _create_brief(fixtures_b)

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()
    fixtures_a = {"client": client_a, "csrf_token": csrf_a, "campaign_id": body_a["campaign"]["id"]}

    response = _post(fixtures_a, _pieces_path(fixtures_a, brief_id), _piece_payload())
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


# --- cardinality / duplicate semantics ------------------------------------------


def test_two_sequential_creates_against_the_same_brief_both_succeed(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)

    first = _post(fixtures, _pieces_path(fixtures, brief_id), _piece_payload())
    second = _post(fixtures, _pieces_path(fixtures, brief_id), _piece_payload())
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["piece"]["id"] != second.json()["piece"]["id"]
    assert first.json()["latest_version"]["id"] != second.json()["latest_version"]["id"]


def test_two_identical_creates_against_the_same_brief_both_succeed(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    payload = _piece_payload()

    first = _post(fixtures, _pieces_path(fixtures, brief_id), payload)
    second = _post(fixtures, _pieces_path(fixtures, brief_id), payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["piece"]["id"] != second.json()["piece"]["id"]


# --- Case G / Case E -------------------------------------------------------------


def test_case_g_generic_plan_piece_makes_no_experiment_claim(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    response = _post(fixtures, _pieces_path(fixtures, brief_id), _piece_payload())
    assert response.status_code == 201, response.text
    assert "experiment_id" not in response.json()["piece"]


def test_case_e_experiment_derived_plan_piece_succeeds(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    experiment_id = _build_experiment_chain(fixtures)
    _plan_id, brief_id = _create_brief(
        fixtures, summary="Operationalizes the experiment.", experiment_public_id=experiment_id
    )
    response = _post(fixtures, _pieces_path(fixtures, brief_id), _piece_payload())
    assert response.status_code == 201, response.text
    assert "experiment_id" not in response.json()["piece"]


# --- historical ContentPlan eligibility (MVP-35A §K, mandatory) -----------------


def test_historical_brief_piece_creation_succeeds(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    plan_v1_id, brief_id = _create_brief(fixtures, summary="Version 1.")

    v2 = _post(fixtures, _plan_path(fixtures), {"summary": "Version 2.", "items": []})
    assert v2.status_code == 201 and v2.json()["plan"]["version"] == 2

    response = _post(fixtures, _pieces_path(fixtures, brief_id), _piece_payload())
    assert response.status_code == 201, response.text


# --- no update/delete surface ---------------------------------------------------


def test_no_patch_put_or_delete_route_exists(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    path = _pieces_path(fixtures, brief_id)
    assert fixtures["client"].patch(path, json={}, headers=headers).status_code == 405
    assert fixtures["client"].put(path, json={}, headers=headers).status_code == 405
    assert fixtures["client"].delete(path, headers=headers).status_code == 405
    assert fixtures["client"].get(path, headers=headers).status_code == 405


# --- readback: existing GET /content surface -------------------------------------


def test_created_piece_is_visible_via_get_content_list(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    created = _post(fixtures, _pieces_path(fixtures, brief_id), _piece_payload())
    assert created.status_code == 201

    listing = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").json()
    assert created.json()["piece"]["id"] in {item["id"] for item in listing["items"]}


def test_created_piece_is_visible_via_get_content_detail(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    created = _post(fixtures, _pieces_path(fixtures, brief_id), _piece_payload())
    piece_id = created.json()["piece"]["id"]

    detail = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{piece_id}").json()
    assert detail["piece"]["id"] == piece_id
    assert detail["latest_version"]["id"] == created.json()["latest_version"]["id"]


# --- lifecycle compatibility -----------------------------------------------------


def test_governed_piece_proceeds_through_existing_lifecycle_to_approval(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)
    created = _post(fixtures, _pieces_path(fixtures, brief_id), _piece_payload())
    piece_id = created.json()["piece"]["id"]
    content_path = f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{piece_id}"
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}

    assert fixtures["client"].post(f"{content_path}/mark-in-production", headers=headers).status_code == 200
    assert fixtures["client"].post(f"{content_path}/mark-produced", headers=headers).status_code == 200
    assert fixtures["client"].post(f"{content_path}/mark-ready-for-review", headers=headers).status_code == 200
    approval_response = fixtures["client"].post(f"{content_path}/request-approval", headers=headers)
    assert approval_response.status_code == 201, approval_response.text
    assert approval_response.json()["latest_approval"]["status"] == "REQUESTED"


# --- downstream non-effects -------------------------------------------------------


def test_downstream_non_effects_exact_row_counts(campaign_run_client: dict, db_session) -> None:
    from app.assets.models import Asset, AssetVersion, CreativeBrief
    from app.content.models import ContentApproval, ContentBrief, ContentDistribution
    from app.learning.models import LearningDerivation, StrategicRecommendationCandidate
    from app.orchestration.models import StrategicApproval, StrategicDecision, StrategyRevision
    from app.planning.models import ContentPlan, PlanItem
    from app.strategy.models import Experiment, Hypothesis, Strategy
    from app.tracking.models import TrackingRequirement

    fixtures = campaign_run_client
    _plan_id, brief_id = _create_brief(fixtures)

    tables = [
        CreativeBrief, Asset, AssetVersion, ContentApproval, ContentDistribution,
        TrackingRequirement, LearningDerivation, StrategicRecommendationCandidate,
        StrategicDecision, StrategicApproval, StrategyRevision, Strategy, Hypothesis,
        Experiment, ContentPlan, PlanItem, ContentBrief,
    ]
    db_session.expire_all()
    before = {t: db_session.execute(select(func.count()).select_from(t)).scalar_one() for t in tables}

    response = _post(fixtures, _pieces_path(fixtures, brief_id), _piece_payload())
    assert response.status_code == 201, response.text

    db_session.expire_all()
    after = {t: db_session.execute(select(func.count()).select_from(t)).scalar_one() for t in tables}

    for t in tables:
        assert after[t] == before[t], f"{t.__name__} changed unexpectedly: {before[t]} -> {after[t]}"
