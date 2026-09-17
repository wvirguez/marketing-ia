"""API contract, authorization, tenancy, and non-leakage tests for
StrategicDecision (MVP-28B, implementing the frozen MVP-28A/-R1/-R2
contract). All marked `postgres`.

No production path creates a StrategicRecommendationCandidate/decides it
from HTTP alone without a full Learning/Implication ancestry, so every
fixture below is built via the real, production service layer against a
genuinely separate, immediately-committed session bound to the app's own
engine — the same pattern ``tests/test_learning_maturation_api.py``'s own
``_record_recommendation`` helper uses.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as OrmSession

from app.campaigns.repository import CampaignRepository
from app.learning.models import LearningCandidateStatus, StrategicRecommendationDecision
from app.learning.service import LearningService
from app.measurement.models import MetricSource
from app.measurement.service import MeasurementService
from app.persistence.session import get_engine
from app.workspaces.models import MembershipRole
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.contenttest import make_user
from tests.learningtest import seed_sufficient_qualification
from tests.measurementtest import default_metric_values, default_period, next_client_request_id
from tests.settingstest import add_member_to_workspace, login_as

pytestmark = pytest.mark.postgres


def _build_recommendation_in_campaign(session, campaign):
    """Same ancestry ``tests/learningtest.py::build_recommendation`` builds,
    but reusing an already-existing ``campaign`` (needed for API tests
    where the campaign was already created through the real HTTP endpoint)
    instead of creating a brand-new Organization/Workspace/Campaign.
    Returns the undecided StrategicRecommendationCandidate."""
    measurement = MeasurementService(session)
    period_start, period_end = default_period()
    entry = measurement.record_metric_entry(
        campaign=campaign, period_start=period_start, period_end=period_end, channel="Instagram",
        source=MetricSource.MANUAL, client_request_id=next_client_request_id(), metric_values=default_metric_values(),
    )
    observation = measurement.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("5.0"))
    signal = measurement.record_signal(campaign=campaign, observations=[observation], summary="CTR trending up.")
    analysis_result = measurement.record_analysis_result(campaign=campaign, signals=[signal], summary="Durable improvement.")

    learning = LearningService(session)
    candidate = learning.record_learning_candidate(analysis_result=analysis_result, summary="Shorter hooks correlate with completion.")
    seed_sufficient_qualification(session, candidate)
    candidate = learning.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.PROVISIONAL)
    candidate = learning.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.VALIDATION_PENDING)
    candidate = learning.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.VALIDATED)
    implication = learning.record_strategic_implication(learning_candidate=candidate, statement="Shorter hooks generalize here.")
    return learning.record_strategic_recommendation_candidate(
        campaign=campaign, learning_candidate=candidate, strategic_implication_public_id=implication.public_id,
        summary="Shift toward shorter hooks.",
    )


def _build_accepted_recommendation(campaign_public_id: str) -> str:
    """Same shape as ``test_learning_maturation_api.py``'s own
    ``_record_recommendation`` helper, extended one step further to
    ACCEPTED — the sole implemented StrategicDecision origin (Model C).
    Returns the Recommendation's public_id."""
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_public_id)
        recommendation = _build_recommendation_in_campaign(session, campaign)
        actor = make_user(session)
        session.flush()
        recommendation = LearningService(session).decide_strategic_recommendation_candidate(
            campaign=campaign, recommendation_public_id=recommendation.public_id,
            decision=StrategicRecommendationDecision.ACCEPTED, actor_user_id=actor.id,
        )
        session.commit()
        return recommendation.public_id


def _path(fixtures: dict, suffix: str = "") -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/strategic-decisions{suffix}"


def _post(fixtures: dict, path: str, json: dict) -> object:
    return fixtures["client"].post(path, json=json, headers={"X-CSRF-Token": fixtures["csrf_token"]})


def _record(fixtures: dict, recommendation_id: str, decision_type: str = "ADOPT", statement: str = "Adopt this direction.") -> object:
    return _post(
        fixtures, _path(fixtures),
        {"strategic_recommendation_candidate_id": recommendation_id, "decision_type": decision_type, "statement": statement},
    )


# --- happy path: OWNER can record and supersede -----------------------------


def test_owner_can_record_and_supersede_a_strategic_decision(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])

    created = _record(fixtures, recommendation_id)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["decision_type"] == "ADOPT"
    assert body["current"] is True
    assert body["strategic_recommendation_candidate_id"] == recommendation_id
    assert body["superseded_at"] is None

    superseded = _post(
        fixtures, _path(fixtures, f"/{body['id']}/supersede"),
        {"decision_type": "DEFER", "statement": "On reflection, defer."},
    )
    assert superseded.status_code == 201, superseded.text
    replacement = superseded.json()
    assert replacement["decision_type"] == "DEFER"
    assert replacement["current"] is True
    assert replacement["id"] != body["id"]

    listing = fixtures["client"].get(_path(fixtures)).json()
    ids = {item["id"] for item in listing["items"]}
    assert {body["id"], replacement["id"]} == ids
    original_in_list = next(item for item in listing["items"] if item["id"] == body["id"])
    assert original_in_list["current"] is False
    assert original_in_list["superseded_by_strategic_decision_id"] == replacement["id"]

    detail = fixtures["client"].get(_path(fixtures, f"/{replacement['id']}")).json()
    assert detail["id"] == replacement["id"]


def test_admin_can_record_a_strategic_decision(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    admin = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.ADMIN)
    admin_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(admin_client, email=admin["email"], password=admin["password"])
    admin_fixtures = {**fixtures, "client": admin_client, "csrf_token": token}

    response = _record(admin_fixtures, recommendation_id)
    assert response.status_code == 201 and response.json()["decision_type"] == "ADOPT"


# --- application authority: MEMBER cannot write -----------------------------


def test_member_cannot_record_a_strategic_decision(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}

    response = _record(member_fixtures, recommendation_id)
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"

    # Member can still read.
    listing = member_client.get(_path(fixtures)).json()
    assert listing["items"] == []

    # No mutation occurred: OWNER can still record it afterward.
    owner_response = _record(fixtures, recommendation_id)
    assert owner_response.status_code == 201


def test_member_cannot_supersede_a_strategic_decision(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    decision_id = _record(fixtures, recommendation_id).json()["id"]

    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])

    response = member_client.post(
        _path(fixtures, f"/{decision_id}/supersede"),
        json={"decision_type": "DECLINE", "statement": "x"}, headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


# --- CSRF --------------------------------------------------------------------


def test_record_requires_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    response = fixtures["client"].post(
        _path(fixtures), json={"strategic_recommendation_candidate_id": recommendation_id, "decision_type": "ADOPT", "statement": "x"}
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_INVALID"


# --- malformed request ---------------------------------------------------


def test_record_rejects_unknown_decision_type(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    response = _post(
        fixtures, _path(fixtures),
        {"strategic_recommendation_candidate_id": recommendation_id, "decision_type": "REJECT", "statement": "x"},
    )
    assert response.status_code == 422


def test_record_rejects_empty_statement(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    response = _post(
        fixtures, _path(fixtures),
        {"strategic_recommendation_candidate_id": recommendation_id, "decision_type": "ADOPT", "statement": ""},
    )
    assert response.status_code == 422


def test_record_rejects_unknown_extra_field(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    response = _post(
        fixtures, _path(fixtures),
        {
            "strategic_recommendation_candidate_id": recommendation_id, "decision_type": "ADOPT", "statement": "x",
            "current": True,
        },
    )
    assert response.status_code == 422


# --- domain gate surfaced over HTTP ------------------------------------------


def test_record_against_a_not_yet_accepted_recommendation_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        recommendation = _build_recommendation_in_campaign(session, campaign)
        session.commit()
        recommendation_public_id = recommendation.public_id

    response = _record(fixtures, recommendation_public_id)
    assert response.status_code == 409 and response.json()["error"]["code"] == "STRATEGIC_RECOMMENDATION_NOT_ACCEPTED"


def test_second_first_record_attempt_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    first = _record(fixtures, recommendation_id)
    assert first.status_code == 201
    second = _record(fixtures, recommendation_id, decision_type="DEFER")
    assert second.status_code == 409 and second.json()["error"]["code"] == "STRATEGIC_DECISION_ALREADY_EXISTS"


def test_second_supersede_attempt_is_a_409(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    decision_id = _record(fixtures, recommendation_id).json()["id"]
    first = _post(fixtures, _path(fixtures, f"/{decision_id}/supersede"), {"decision_type": "DEFER", "statement": "x"})
    assert first.status_code == 201
    second = _post(fixtures, _path(fixtures, f"/{decision_id}/supersede"), {"decision_type": "DECLINE", "statement": "y"})
    assert second.status_code == 409 and second.json()["error"]["code"] == "STRATEGIC_DECISION_ALREADY_SUPERSEDED"


# --- no generic mutation surface --------------------------------------------


def test_no_patch_or_delete_route_exists(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    recommendation_id = _build_accepted_recommendation(fixtures["campaign_id"])
    decision_id = _record(fixtures, recommendation_id).json()["id"]
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    assert fixtures["client"].patch(_path(fixtures, f"/{decision_id}"), json={"statement": "y"}, headers=headers).status_code == 405
    assert fixtures["client"].delete(_path(fixtures, f"/{decision_id}"), headers=headers).status_code == 405


# --- tenancy: non-leaky campaign-scoped resolution --------------------------


def test_unknown_decision_is_forbidden_non_leakily(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].get(_path(fixtures, "/DEC-TOTALLYFAKE0"))
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_tenant_a_cannot_read_or_supersede_tenant_bs_decision(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    campaign_b_id = body_b["campaign"]["id"]
    recommendation_b = _build_accepted_recommendation(campaign_b_id)
    decision_b = client_b.post(
        f"/api/v1/campaigns/{campaign_b_id}/strategic-decisions",
        json={"strategic_recommendation_candidate_id": recommendation_b, "decision_type": "ADOPT", "statement": "B's own decision."},
        headers={"X-CSRF-Token": csrf_b},
    ).json()

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()
    campaign_a_id = body_a["campaign"]["id"]

    read_response = client_a.get(f"/api/v1/campaigns/{campaign_a_id}/strategic-decisions/{decision_b['id']}")
    assert read_response.status_code == 403 and read_response.json()["error"]["code"] == "FORBIDDEN"

    supersede_response = client_a.post(
        f"/api/v1/campaigns/{campaign_a_id}/strategic-decisions/{decision_b['id']}/supersede",
        json={"decision_type": "DEFER", "statement": "Cross-tenant attempt."}, headers={"X-CSRF-Token": csrf_a},
    )
    assert supersede_response.status_code == 403 and supersede_response.json()["error"]["code"] == "FORBIDDEN"

    # No mutation: B's own decision is still current and reachable.
    still_there = client_b.get(f"/api/v1/campaigns/{campaign_b_id}/strategic-decisions/{decision_b['id']}").json()
    assert still_there["current"] is True


def test_record_against_a_recommendation_from_a_different_campaign_is_forbidden(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client  # Campaign A
    body_b = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B"), headers={"X-CSRF-Token": fixtures["csrf_token"]}
    ).json()
    campaign_b_id = body_b["campaign"]["id"]
    recommendation_b = _build_accepted_recommendation(campaign_b_id)

    # Campaign A's URL targeting Campaign B's Recommendation must fail non-leakily.
    response = _record(fixtures, recommendation_b)
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"

    # No mutation: Campaign B's own URL still works correctly.
    correct = fixtures["client"].post(
        f"/api/v1/campaigns/{campaign_b_id}/strategic-decisions",
        json={"strategic_recommendation_candidate_id": recommendation_b, "decision_type": "ADOPT", "statement": "B's own."},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert correct.status_code == 201
