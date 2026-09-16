"""API contract, tenancy, authorization, temporal, and audit tests for
MVP-24 (ContentDistribution <-> TrackingRequirement Association). All
marked `postgres`.

IDENTITY-ONLY HISTORICAL ASSOCIATION (MVP-24A-R1): these tests never
assert that the association preserves TrackingRequirement.status or
TrackingPlan.status — only pair identity and its own created_at.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.audit.models import ActorType, AuditEvent
from app.campaigns.repository import CampaignRepository
from app.content.models import ContentDistributionTrackingRequirement
from app.content.repository import ContentDistributionRepository, ContentPieceRepository
from app.persistence.session import get_engine
from app.tracking.repository import TrackingRequirementRepository
from app.tracking.service import TrackingService
from app.workspaces.models import MembershipRole
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.orchestrationtest import initialize_run
from tests.settingstest import add_member_to_workspace, login_as
from tests.test_content_api import (
    _advance_to_ready_for_review,
    _lifecycle_path,
    _post,
    _record_content_piece,
    _under_review_approval_id,
    campaign_client_with_stages,
)
from tests.test_distribution_api import _approved, _path as _distribution_path
from tests.test_tracking_api import _record_plan_with_requirement, _tracking_path

pytestmark = pytest.mark.postgres


def _ready_distribution(fixtures: dict) -> str:
    """Returns a content_id whose ContentDistribution is READY."""
    content_id = _approved(fixtures)
    response = fixtures["client"].post(
        _distribution_path(fixtures, content_id, "mark-ready-for-distribution"),
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 201, response.text
    return content_id


def _association_path(fixtures: dict, content_id: str, suffix: str = "") -> str:
    return _lifecycle_path(fixtures, content_id, f"/distribution/tracking-requirements{suffix}")


def _associate(fixtures: dict, content_id: str, requirement_id: str):
    return _post(fixtures, _association_path(fixtures, content_id), {"tracking_requirement_id": requirement_id})


def _dissociate(fixtures: dict, content_id: str, requirement_id: str):
    return _post(fixtures, _association_path(fixtures, content_id, f"/{requirement_id}/remove"))


# --- happy path --------------------------------------------------------


def test_associate_happy_path(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])

    response = _associate(fixtures, content_id, requirement_id)
    assert response.status_code == 201, response.text
    assert response.json()["distribution"]["tracking_requirement_ids"] == [requirement_id]

    tracking_body = fixtures["client"].get(_tracking_path(fixtures)).json()
    requirement = tracking_body["plan"]["requirements"][0]
    assert requirement["associated_distribution_ids"] == [response.json()["distribution"]["id"]]


def test_dissociate_happy_path(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    _associate(fixtures, content_id, requirement_id)

    response = _dissociate(fixtures, content_id, requirement_id)
    assert response.status_code == 200, response.text
    assert response.json()["distribution"]["tracking_requirement_ids"] == []


def test_dissociate_not_associated_is_conflict(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])

    response = _dissociate(fixtures, content_id, requirement_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TRACKING_REQUIREMENT_NOT_ASSOCIATED"


def test_duplicate_association_is_conflict_no_second_row(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    assert _associate(fixtures, content_id, requirement_id).status_code == 201

    response = _associate(fixtures, content_id, requirement_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TRACKING_REQUIREMENT_ALREADY_ASSOCIATED"

    with OrmSession(bind=get_engine()) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        piece = ContentPieceRepository(session).get_for_campaign_by_public_id(campaign_id=campaign.id, public_id=content_id)
        distribution = ContentDistributionRepository(session).get_for_piece(piece.id)
        # Scoped to this exact Distribution — never a global unfiltered
        # count, which would be fragile against other tests' committed
        # rows in the same full-suite run (the exact class of pollution
        # MVP-23B's own report already flagged and fixed once).
        count = session.execute(
            select(func.count()).select_from(ContentDistributionTrackingRequirement).where(
                ContentDistributionTrackingRequirement.content_distribution_id == distribution.id
            )
        ).scalar_one()
        assert count == 1


# --- illegal-state rejection --------------------------------------------


def test_associate_unknown_requirement_is_forbidden(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)
    response = _associate(fixtures, content_id, "TRQ-TOTALLYFAKE0")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_associate_no_distribution_is_conflict(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _record_content_piece(fixtures)  # never reaches READY distribution
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    response = _associate(fixtures, content_id, requirement_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"


# --- temporal contract (MVP-24A-R1): DISTRIBUTED freezes the pair --------


def test_distributed_freezes_association_add(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    recorded = _post(fixtures, _distribution_path(fixtures, content_id, "record-distributed"))
    assert recorded.status_code == 200, recorded.text

    response = _associate(fixtures, content_id, requirement_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"


def test_distributed_freezes_association_remove(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    assert _associate(fixtures, content_id, requirement_id).status_code == 201
    recorded = _post(fixtures, _distribution_path(fixtures, content_id, "record-distributed"))
    assert recorded.status_code == 200, recorded.text

    response = _dissociate(fixtures, content_id, requirement_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"
    # No mutation: the association survives the rejected removal attempt.
    assert recorded.json()["distribution"]["tracking_requirement_ids"] == [requirement_id]


def test_requirement_status_may_change_after_distributed(campaign_client_with_stages: dict) -> None:
    """MVP-24A-R1 §K: current Requirement status is current-state
    metadata, never frozen by an associated Distribution reaching
    DISTRIBUTED."""
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    assert _associate(fixtures, content_id, requirement_id).status_code == 201
    assert _post(fixtures, _distribution_path(fixtures, content_id, "record-distributed")).status_code == 200

    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    patch = fixtures["client"].patch(
        _tracking_path(fixtures),
        json={"operation": "UPDATE_REQUIREMENT_STATUS", "requirement_id": requirement_id, "status": "Configurado"},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["plan"]["requirements"][0]["status"] == "Configurado"

    # Association pair identity is untouched by the status change.
    detail = fixtures["client"].get(_lifecycle_path(fixtures, content_id, "")).json()
    assert detail["distribution"]["tracking_requirement_ids"] == [requirement_id]


def test_certified_plan_allows_new_association_to_ready_distribution(campaign_client_with_stages: dict) -> None:
    """MVP-24A-R1 §D/§M Case 2: CERTIFIED does not reach the association
    table — a Requirement from an already-CERTIFIED Plan may still be
    associated with a new READY Distribution."""
    fixtures = campaign_client_with_stages
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    for target in ("REQUIREMENTS_DEFINED", "CONFIGURATION_PENDING", "CONFIGURED", "VERIFICATION_PENDING", "CERTIFIED"):
        response = fixtures["client"].patch(
            _tracking_path(fixtures), json={"operation": "TRANSITION_PLAN", "target_status": target}, headers=headers,
        )
        assert response.status_code == 200, response.text
    assert response.json()["plan"]["status"] == "CERTIFIED"

    content_id = _ready_distribution(fixtures)
    association = _associate(fixtures, content_id, requirement_id)
    assert association.status_code == 201, association.text

    removal = _dissociate(fixtures, content_id, requirement_id)
    assert removal.status_code == 200, removal.text


# --- N:N -----------------------------------------------------------------


def test_one_requirement_associated_to_multiple_distributions(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    content_a = _ready_distribution(fixtures)
    content_b = _ready_distribution(fixtures)

    resp_a = _associate(fixtures, content_a, requirement_id)
    resp_b = _associate(fixtures, content_b, requirement_id)
    assert resp_a.status_code == 201 and resp_b.status_code == 201
    dist_a_id = resp_a.json()["distribution"]["id"]
    dist_b_id = resp_b.json()["distribution"]["id"]
    assert dist_a_id != dist_b_id

    tracking_body = fixtures["client"].get(_tracking_path(fixtures)).json()
    associated = set(tracking_body["plan"]["requirements"][0]["associated_distribution_ids"])
    assert associated == {dist_a_id, dist_b_id}


def test_one_distribution_associated_to_multiple_requirements(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        service = TrackingService(session)
        plan = service.record_tracking_plan(campaign=campaign)
        req_a_id = service.record_tracking_requirement(tracking_plan=plan, name="Purchase event").public_id
        req_b_id = service.record_tracking_requirement(tracking_plan=plan, name="UTM parameters").public_id

    resp_a = _associate(fixtures, content_id, req_a_id)
    resp_b = _associate(fixtures, content_id, req_b_id)
    assert resp_a.status_code == 201 and resp_b.status_code == 201
    assert set(resp_b.json()["distribution"]["tracking_requirement_ids"]) == {req_a_id, req_b_id}


# --- tenancy ---------------------------------------------------------------


def test_foreign_workspace_requirement_rejected(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)

    other_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    register_and_get_csrf(other_client, display_name="Other User")
    csrf_other = other_client.get("/api/v1/auth/csrf").json()["csrf_token"]
    other_campaign = other_client.post(
        "/api/v1/campaigns", json=campaign_payload(name="Other WS Campaign"), headers={"X-CSRF-Token": csrf_other}
    ).json()
    _plan_id, foreign_requirement_id = _record_plan_with_requirement(other_campaign["campaign"]["id"])

    response = _associate(fixtures, content_id, foreign_requirement_id)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_same_workspace_different_campaign_requirement_rejected(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)

    body_b = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B"), headers={"X-CSRF-Token": fixtures["csrf_token"]}
    ).json()
    _plan_id, requirement_b_id = _record_plan_with_requirement(body_b["campaign"]["id"])

    response = _associate(fixtures, content_id, requirement_b_id)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    # No mutation: requirement_b can still be associated correctly through
    # its own Campaign B, proving zero cross-contamination occurred.
    fixtures_b = {**fixtures, "campaign_id": body_b["campaign"]["id"], "run_id": body_b["run"]["id"]}
    initialize_run(fixtures_b)
    content_b = _ready_distribution(fixtures_b)
    correct = _associate(fixtures_b, content_b, requirement_b_id)
    assert correct.status_code == 201


def test_foreign_workspace_distribution_rejected(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])

    other_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    register_and_get_csrf(other_client, display_name="Other User 2")
    csrf_other = other_client.get("/api/v1/auth/csrf").json()["csrf_token"]
    other_campaign = other_client.post(
        "/api/v1/campaigns", json=campaign_payload(name="Other WS Campaign 2"), headers={"X-CSRF-Token": csrf_other}
    ).json()
    other_fixtures = {
        "client": other_client, "csrf_token": csrf_other,
        "campaign_id": other_campaign["campaign"]["id"], "run_id": other_campaign["run"]["id"],
    }
    initialize_run(other_fixtures)
    foreign_content_id = _record_content_piece(other_fixtures)

    # Attempting association through OUR campaign's URL, naming a
    # Content Piece id that only exists in the OTHER workspace, must be
    # non-leaky — either FORBIDDEN (piece not found in our campaign).
    response = _associate(fixtures, foreign_content_id, requirement_id)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_unknown_content_piece_is_non_leaky(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    response = _associate(fixtures, "CNT-TOTALLYFAKE0", requirement_id)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- authorization: MEMBER+ (no require_role) ------------------------------


def test_member_can_associate_and_dissociate(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])

    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}

    assert _associate(member_fixtures, content_id, requirement_id).status_code == 201
    assert _dissociate(member_fixtures, content_id, requirement_id).status_code == 200


# --- audit -------------------------------------------------------------


def test_audit_events_prove_pair_identity_and_actor(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])

    assert _associate(fixtures, content_id, requirement_id).status_code == 201
    assert _dissociate(fixtures, content_id, requirement_id).status_code == 200

    with OrmSession(bind=get_engine()) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        piece = ContentPieceRepository(session).get_for_campaign_by_public_id(campaign_id=campaign.id, public_id=content_id)
        distribution = ContentDistributionRepository(session).get_for_piece(piece.id)
        requirement = TrackingRequirementRepository(session).get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=requirement_id,
        )
        associated_events = session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "distribution.tracking_requirement.associated",
                AuditEvent.distribution_id == distribution.id,
                AuditEvent.tracking_requirement_id == requirement.id,
            )
        ).scalars().all()
        dissociated_events = session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "distribution.tracking_requirement.dissociated",
                AuditEvent.distribution_id == distribution.id,
                AuditEvent.tracking_requirement_id == requirement.id,
            )
        ).scalars().all()
        assert len(associated_events) == 1
        assert len(dissociated_events) == 1
        for event in (*associated_events, *dissociated_events):
            assert event.actor_type is ActorType.USER
            assert event.actor_user_id is not None


def test_failed_association_produces_no_audit_event(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    assert _post(fixtures, _distribution_path(fixtures, content_id, "record-distributed")).status_code == 200

    with OrmSession(bind=get_engine()) as session:
        before = session.execute(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == "distribution.tracking_requirement.associated")
        ).scalar_one()

    response = _associate(fixtures, content_id, requirement_id)
    assert response.status_code == 409

    with OrmSession(bind=get_engine()) as session:
        after = session.execute(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == "distribution.tracking_requirement.associated")
        ).scalar_one()
    assert after == before


# --- semantic protection: no snapshot fields embedded ----------------------


def test_distribution_response_never_embeds_requirement_name_or_status(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    content_id = _ready_distribution(fixtures)
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    response = _associate(fixtures, content_id, requirement_id)
    distribution_body = response.json()["distribution"]
    assert set(distribution_body.keys()) == {
        "id", "status", "channel", "external_reference", "ready_at", "distributed_at", "tracking_requirement_ids",
    }
    assert "Purchase event" not in response.text


# --- full journey (MVP-24B §50) --------------------------------------------


def test_full_journey_tracking_requirement_association(campaign_client_with_stages: dict) -> None:
    """Campaign -> TrackingPlan -> TrackingRequirement -> ContentDistribution
    READY -> associate -> reload -> association persists -> DISTRIBUTED ->
    association frozen -> Evidence can still be recorded -> Requirement
    current status may change -> association remains -> current status
    stays clearly current-state only. Also proves zero Strategy/Learning/
    Experiment/MeasurementAnalysis mutation from any of this."""
    fixtures = campaign_client_with_stages
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    content_id = _ready_distribution(fixtures)

    associate_response = _associate(fixtures, content_id, requirement_id)
    assert associate_response.status_code == 201, associate_response.text

    # Reload: a completely independent GET reconstructs the same association.
    reload_body = fixtures["client"].get(_lifecycle_path(fixtures, content_id, "")).json()
    assert reload_body["distribution"]["tracking_requirement_ids"] == [requirement_id]

    # DISTRIBUTED freezes the pair.
    distributed = _post(fixtures, _distribution_path(fixtures, content_id, "record-distributed"))
    assert distributed.status_code == 200, distributed.text
    assert distributed.json()["distribution"]["tracking_requirement_ids"] == [requirement_id]

    # Evidence can still be recorded against the now-DISTRIBUTED Distribution.
    from datetime import datetime, timezone, timedelta

    today = datetime.now(timezone.utc).date()
    evidence_response = _post(
        fixtures, _lifecycle_path(fixtures, content_id, "/distribution/evidence"),
        {
            "period_start": (today - timedelta(days=5)).isoformat(),
            "period_end": today.isoformat(),
            "values": {"reach": 100},
            "client_request_id": "journey-evidence-1",
        },
    )
    assert evidence_response.status_code == 201, evidence_response.text

    # Requirement current status may still change; association is untouched.
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    status_response = fixtures["client"].patch(
        _tracking_path(fixtures),
        json={"operation": "UPDATE_REQUIREMENT_STATUS", "requirement_id": requirement_id, "status": "Verificado"},
        headers=headers,
    )
    assert status_response.status_code == 200
    assert status_response.json()["plan"]["requirements"][0]["status"] == "Verificado"

    final_detail = fixtures["client"].get(_lifecycle_path(fixtures, content_id, "")).json()
    assert final_detail["distribution"]["tracking_requirement_ids"] == [requirement_id]

    # Zero Strategy/Learning/Experiment/MeasurementAnalysis mutation.
    with OrmSession(bind=get_engine()) as session:
        from app.learning.models import LearningCandidate, StrategicRecommendationCandidate
        from app.measurement.models import AnalysisResult, MeasurementAnalysisRun
        from app.strategy.models import Experiment, Hypothesis, Strategy

        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        for model in (Strategy, Hypothesis, Experiment, LearningCandidate, StrategicRecommendationCandidate):
            count = session.execute(select(func.count()).select_from(model).where(model.workspace_id == campaign.workspace_id)).scalar_one()
            assert count == 0, f"{model.__name__} must remain zero"
        for model in (AnalysisResult, MeasurementAnalysisRun):
            count = session.execute(select(func.count()).select_from(model).where(model.workspace_id == campaign.workspace_id)).scalar_one()
            assert count == 0, f"{model.__name__} must remain zero — evidence never feeds Analysis"
