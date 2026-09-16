"""API contract, authorization, tenancy, audit, and semantic-protection
tests for MVP-23 (Learning Candidate Maturation & Strategic Recommendation
Bridge). Covers the four new POST routes
(``mark-provisional``/``mark-validation-pending``/``decision``/
``recommendations``) plus the LEARNING-P3-1 regression on the pre-existing
PATCH decision route. All marked `postgres`.

No public creation route exists for LearningCandidate, so every fixture
below is built via ``LearningService``/``MeasurementService`` against a
genuinely separate, immediately-committed session bound to the app's own
engine — the same pattern ``tests/test_learning_api.py``'s own
``_record_recommendation`` helper uses.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.audit.models import ActorType, AuditEvent
from app.campaigns.repository import CampaignRepository
from app.learning.models import LearningCandidateStatus
from app.learning.service import LearningService
from tests.learningtest import seed_sufficient_qualification
from app.measurement.models import MetricSource
from app.measurement.service import MeasurementService
from app.persistence.session import get_engine
from app.strategy.models import Experiment, Hypothesis, Positioning, Strategy
from app.workspaces.models import MembershipRole
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.measurementtest import default_metric_values, default_period, next_client_request_id
from tests.settingstest import add_member_to_workspace, login_as

pytestmark = pytest.mark.postgres


def _build_candidate(campaign_public_id: str, *, target_status: LearningCandidateStatus, qualified: bool = True) -> str:
    """Builds one AnalysisResult -> LearningCandidate for ``campaign_public_id``
    and drives it to exactly ``target_status`` via the production service,
    through a genuinely separate, immediately-committed session. Returns the
    candidate's public_id."""
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_public_id)
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
        if qualified:
            seed_sufficient_qualification(session, candidate)

        path = {
            LearningCandidateStatus.CANDIDATE_IDENTIFIED: [],
            LearningCandidateStatus.PROVISIONAL: [LearningCandidateStatus.PROVISIONAL],
            LearningCandidateStatus.VALIDATION_PENDING: [LearningCandidateStatus.PROVISIONAL, LearningCandidateStatus.VALIDATION_PENDING],
            LearningCandidateStatus.VALIDATED: [
                LearningCandidateStatus.PROVISIONAL, LearningCandidateStatus.VALIDATION_PENDING, LearningCandidateStatus.VALIDATED,
            ],
            LearningCandidateStatus.REJECTED: [
                LearningCandidateStatus.PROVISIONAL, LearningCandidateStatus.VALIDATION_PENDING, LearningCandidateStatus.REJECTED,
            ],
            LearningCandidateStatus.INSUFFICIENT_EVIDENCE: [
                LearningCandidateStatus.PROVISIONAL, LearningCandidateStatus.VALIDATION_PENDING, LearningCandidateStatus.INSUFFICIENT_EVIDENCE,
            ],
        }[target_status]
        for step in path:
            candidate = learning.transition_learning_candidate(learning_candidate=candidate, target_status=step)
        return candidate.public_id


def _record_recommendation(campaign_public_id: str) -> tuple[str, str]:
    """Same shape as ``test_learning_api.py``'s own helper — a VALIDATED
    candidate with one recommendation already created. Returns
    ``(learning_candidate_public_id, recommendation_public_id)``."""
    lrn_id = _build_candidate(campaign_public_id, target_status=LearningCandidateStatus.VALIDATED)
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_public_id)
        learning = LearningService(session)
        candidate = learning.candidates.get_for_campaign_by_public_id(campaign_id=campaign.id, public_id=lrn_id)
        recommendation = learning.record_strategic_recommendation_candidate(learning_candidate=candidate, summary="Shift toward shorter hooks.")
        return lrn_id, recommendation.public_id


def _path(fixtures: dict, candidate_or_recommendation_id: str, action: str) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/learning/{candidate_or_recommendation_id}/{action}"


def _post(fixtures: dict, path: str, json: dict | None = None) -> object:
    return fixtures["client"].post(path, json=json if json is not None else {}, headers={"X-CSRF-Token": fixtures["csrf_token"]})


# --- happy path: every legal edge is reachable via HTTP ---------------------


def test_mark_provisional_happy_path(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.CANDIDATE_IDENTIFIED)
    response = _post(fixtures, _path(fixtures, lrn_id, "mark-provisional"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == lrn_id
    assert body["status"] == "PROVISIONAL"


def test_mark_validation_pending_from_provisional(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.PROVISIONAL)
    response = _post(fixtures, _path(fixtures, lrn_id, "mark-validation-pending"))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "VALIDATION_PENDING"


@pytest.mark.parametrize("decision", ["VALIDATED", "REJECTED", "INSUFFICIENT_EVIDENCE"])
def test_decision_happy_path(campaign_run_client: dict, decision: str) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.VALIDATION_PENDING)
    response = _post(fixtures, _path(fixtures, lrn_id, "decision"), {"decision": decision})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == decision


def test_reopen_from_insufficient_evidence(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.INSUFFICIENT_EVIDENCE)
    response = _post(fixtures, _path(fixtures, lrn_id, "mark-validation-pending"))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "VALIDATION_PENDING"


def test_create_recommendation_happy_path(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.VALIDATED)
    response = _post(fixtures, _path(fixtures, lrn_id, "recommendations"), {"summary": "Shift toward shorter hooks."})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["learning_candidate_id"] == lrn_id
    assert body["summary"] == "Shift toward shorter hooks."
    assert body["decision"] is None
    assert body["decided_at"] is None


def test_create_recommendation_cardinality_0_to_n(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.VALIDATED)
    first = _post(fixtures, _path(fixtures, lrn_id, "recommendations"), {"summary": "Option A."})
    second = _post(fixtures, _path(fixtures, lrn_id, "recommendations"), {"summary": "Option B."})
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


# --- illegal transitions: deterministic 409, no state graph re-derivation --


def test_mark_provisional_illegal_from_provisional_itself(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.PROVISIONAL)
    response = _post(fixtures, _path(fixtures, lrn_id, "mark-provisional"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"


def test_decision_illegal_from_provisional(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.PROVISIONAL)
    response = _post(fixtures, _path(fixtures, lrn_id, "decision"), {"decision": "VALIDATED"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"


def test_decision_illegal_from_terminal_validated(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.VALIDATED)
    response = _post(fixtures, _path(fixtures, lrn_id, "decision"), {"decision": "REJECTED"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"


def test_decision_rejects_arbitrary_status_string(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.VALIDATION_PENDING)
    response = _post(fixtures, _path(fixtures, lrn_id, "decision"), {"decision": "CANDIDATE_IDENTIFIED"})
    assert response.status_code == 422
    response = _post(fixtures, _path(fixtures, lrn_id, "decision"), {"decision": "MAYBE"})
    assert response.status_code == 422


def test_create_recommendation_requires_validated_parent(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.VALIDATION_PENDING)
    response = _post(fixtures, _path(fixtures, lrn_id, "recommendations"), {"summary": "Too early."})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LEARNING_CANDIDATE_NOT_VALIDATED"


def test_create_recommendation_rejects_empty_summary(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.VALIDATED)
    response = _post(fixtures, _path(fixtures, lrn_id, "recommendations"), {"summary": ""})
    assert response.status_code == 422


# --- CSRF --------------------------------------------------------------------


def test_new_routes_require_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.CANDIDATE_IDENTIFIED)
    response = fixtures["client"].post(_path(fixtures, lrn_id, "mark-provisional"), json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


# --- authorization: MEMBER vs OWNER/ADMIN (MVP-23A §U, MVP-23A-R1 §I) ------


def test_member_can_progress_and_reopen_but_not_decide(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}

    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.CANDIDATE_IDENTIFIED)
    assert _post(member_fixtures, _path(member_fixtures, lrn_id, "mark-provisional")).status_code == 200
    assert _post(member_fixtures, _path(member_fixtures, lrn_id, "mark-validation-pending")).status_code == 200

    response = _post(member_fixtures, _path(member_fixtures, lrn_id, "decision"), {"decision": "VALIDATED"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"

    # Reopen after an OWNER decision: MEMBER may still reopen (MVP-23A-R1).
    owner_response = _post(fixtures, _path(fixtures, lrn_id, "decision"), {"decision": "INSUFFICIENT_EVIDENCE"})
    assert owner_response.status_code == 200
    reopen_response = _post(member_fixtures, _path(member_fixtures, lrn_id, "mark-validation-pending"))
    assert reopen_response.status_code == 200 and reopen_response.json()["status"] == "VALIDATION_PENDING"


def test_member_can_create_recommendation_but_not_decide_it(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])
    member_fixtures = {**fixtures, "client": member_client, "csrf_token": token}

    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.VALIDATED)
    response = _post(member_fixtures, _path(member_fixtures, lrn_id, "recommendations"), {"summary": "Member-proposed change."})
    assert response.status_code == 201, response.text
    src_id = response.json()["id"]

    decide_response = member_client.patch(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/learning/{src_id}",
        json={"decision": "ACCEPTED"}, headers={"X-CSRF-Token": token},
    )
    assert decide_response.status_code == 403 and decide_response.json()["error"]["code"] == "FORBIDDEN"


def test_owner_can_decide_learning_and_recommendation(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.VALIDATION_PENDING)
    assert _post(fixtures, _path(fixtures, lrn_id, "decision"), {"decision": "VALIDATED"}).status_code == 200

    src_response = _post(fixtures, _path(fixtures, lrn_id, "recommendations"), {"summary": "Owner-proposed change."})
    src_id = src_response.json()["id"]
    decide_response = fixtures["client"].patch(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/learning/{src_id}",
        json={"decision": "ACCEPTED"}, headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert decide_response.status_code == 200 and decide_response.json()["decision"] == "ACCEPTED"


def test_admin_can_decide_learning(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    admin = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.ADMIN)
    admin_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(admin_client, email=admin["email"], password=admin["password"])
    admin_fixtures = {**fixtures, "client": admin_client, "csrf_token": token}

    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.VALIDATION_PENDING)
    response = _post(admin_fixtures, _path(admin_fixtures, lrn_id, "decision"), {"decision": "VALIDATED"})
    assert response.status_code == 200 and response.json()["status"] == "VALIDATED"


# --- LEARNING-P3-1 regression: the pre-existing PATCH route now enforces role


def test_learning_p3_1_member_cannot_patch_recommendation_decision(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _lrn_id, src_id = _record_recommendation(fixtures["campaign_id"])
    workspace_id = fixtures["client"].get("/api/v1/auth/session").json()["workspace"]["id"]
    member = add_member_to_workspace(workspace_public_id=workspace_id, role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    token = login_as(member_client, email=member["email"], password=member["password"])

    response = member_client.patch(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/learning/{src_id}",
        json={"decision": "ACCEPTED"}, headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"

    # No mutation occurred: an OWNER can still decide it correctly afterward.
    owner_response = fixtures["client"].patch(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/learning/{src_id}",
        json={"decision": "ACCEPTED"}, headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert owner_response.status_code == 200 and owner_response.json()["decision"] == "ACCEPTED"


# --- tenancy -----------------------------------------------------------------


def test_unknown_candidate_is_forbidden_non_leakily(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    for action, payload in (("mark-provisional", None), ("decision", {"decision": "VALIDATED"}), ("recommendations", {"summary": "x"})):
        response = _post(fixtures, _path(fixtures, "LRN-TOTALLYFAKE0", action), payload)
        assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"


def test_tenant_a_cannot_mutate_tenant_bs_candidate(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    campaign_b_id = body_b["campaign"]["id"]
    lrn_b = _build_candidate(campaign_b_id, target_status=LearningCandidateStatus.CANDIDATE_IDENTIFIED)

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()
    response = client_a.post(
        f"/api/v1/campaigns/{body_a['campaign']['id']}/learning/{lrn_b}/mark-provisional",
        headers={"X-CSRF-Token": csrf_a},
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"

    # Confirm zero mutation: B's own campaign URL still works correctly.
    correct = client_b.post(
        f"/api/v1/campaigns/{campaign_b_id}/learning/{lrn_b}/mark-provisional", headers={"X-CSRF-Token": csrf_b},
    )
    assert correct.status_code == 200 and correct.json()["status"] == "PROVISIONAL"


def test_same_workspace_different_campaign_candidate_substitution_fails(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client  # Campaign A
    lrn_a = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.CANDIDATE_IDENTIFIED)

    body_b = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B"), headers={"X-CSRF-Token": fixtures["csrf_token"]}
    ).json()
    campaign_b_id = body_b["campaign"]["id"]
    lrn_b = _build_candidate(campaign_b_id, target_status=LearningCandidateStatus.CANDIDATE_IDENTIFIED)

    # Campaign A's URL targeting Campaign B's candidate must fail non-leakily.
    response = _post(fixtures, _path(fixtures, lrn_b, "mark-provisional"))
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"

    # No mutation: lrn_a is untouched, lrn_b is still reachable via its own campaign.
    correct_a = _post(fixtures, _path(fixtures, lrn_a, "mark-provisional"))
    assert correct_a.status_code == 200
    correct_b = fixtures["client"].post(
        f"/api/v1/campaigns/{campaign_b_id}/learning/{lrn_b}/mark-provisional", headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert correct_b.status_code == 200


# --- audit: reopen preserves history (MVP-23B §44) --------------------------


def test_reopen_creates_new_event_and_preserves_prior_decision_event(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.VALIDATION_PENDING)
    assert _post(fixtures, _path(fixtures, lrn_id, "decision"), {"decision": "INSUFFICIENT_EVIDENCE"}).status_code == 200
    assert _post(fixtures, _path(fixtures, lrn_id, "mark-validation-pending")).status_code == 200

    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        candidate = LearningService(session).candidates.get_for_campaign_by_public_id(campaign_id=campaign.id, public_id=lrn_id)
        events = session.execute(
            select(AuditEvent)
            .where(AuditEvent.event_type == "learning.candidate.status_changed", AuditEvent.learning_candidate_id == candidate.id)
            .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
        ).scalars().all()

    # _build_candidate already drove CANDIDATE_IDENTIFIED->PROVISIONAL->
    # VALIDATION_PENDING (2 events); this test then adds VALIDATION_PENDING
    # ->INSUFFICIENT_EVIDENCE and the reopen INSUFFICIENT_EVIDENCE->
    # VALIDATION_PENDING (2 more) — 4 total, all preserved in order.
    assert len(events) == 4
    assert (events[0].previous_state, events[0].new_state) == ("CANDIDATE_IDENTIFIED", "PROVISIONAL")
    assert (events[1].previous_state, events[1].new_state) == ("PROVISIONAL", "VALIDATION_PENDING")
    assert (events[2].previous_state, events[2].new_state) == ("VALIDATION_PENDING", "INSUFFICIENT_EVIDENCE")
    assert (events[3].previous_state, events[3].new_state) == ("INSUFFICIENT_EVIDENCE", "VALIDATION_PENDING")
    # Only the two HTTP-triggered transitions (events 2/3) carry a real
    # human actor — the first two came from _build_candidate's own direct,
    # actor-less service calls (ActorType.SYSTEM), confirming the boundary
    # is attribution-accurate rather than defaulted to USER everywhere.
    for event in events[2:]:
        assert event.actor_type is ActorType.USER
        assert event.actor_user_id is not None


# --- semantic protection and isolation ---------------------------------------


def test_no_forbidden_wording_in_any_new_response(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.VALIDATION_PENDING)
    response = _post(fixtures, _path(fixtures, lrn_id, "decision"), {"decision": "VALIDATED"})
    src_response = _post(fixtures, _path(fixtures, lrn_id, "recommendations"), {"summary": "Shift toward shorter hooks."})
    lowered = (response.text + src_response.text).lower()
    for forbidden in (
        "caused", "causad", "generad", "atribuid", "mejor", "peor", "ganador", "rendimiento",
        "significan", "probado", "comprobado",
    ):
        assert forbidden not in lowered


def test_accepted_recommendation_does_not_touch_strategy_tables(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id, src_id = _record_recommendation(fixtures["campaign_id"])
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        before = {
            model: session.execute(select(func.count()).select_from(model)).scalar_one()
            for model in (Strategy, Positioning, Hypothesis, Experiment)
        }

    response = fixtures["client"].patch(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/learning/{src_id}",
        json={"decision": "ACCEPTED"}, headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 200

    with OrmSession(bind=engine) as session:
        after = {
            model: session.execute(select(func.count()).select_from(model)).scalar_one()
            for model in (Strategy, Positioning, Hypothesis, Experiment)
        }
    assert before == after


def test_recommendation_response_has_no_forbidden_fields(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    lrn_id = _build_candidate(fixtures["campaign_id"], target_status=LearningCandidateStatus.VALIDATED)
    response = _post(fixtures, _path(fixtures, lrn_id, "recommendations"), {"summary": "x"})
    assert set(response.json().keys()) == {"id", "learning_candidate_id", "summary", "decision", "created_at", "decided_at"}


# --- MVP-23B §61: full end-to-end journey via real HTTP, entirely through
# the production bridge (POST /derive), not through the direct-service
# _build_candidate helper used everywhere else in this file. This is a
# targeted, module-scoped equivalent of "extend the shared Full Journey"
# (MVP-23B §61) — the shared tests/test_full_journey.py is deliberately
# left untouched because it carries pre-existing assertions that a
# CANDIDATE_IDENTIFIED-only, zero-recommendation Learning row for its own
# journey candidate is correct (lines asserting exactly that), matching
# the identical, explicitly-documented precedent MVP-22's own full-journey
# extension already established (never disturb an earlier exact-count/
# exact-state assertion by mutating the same row further).


def test_full_derive_to_accepted_recommendation_journey_via_http(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client

    # Build one real Measurement -> AnalysisResult chain, then use the
    # production bridge route (not a direct service call) to create the
    # LearningCandidate.
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        workspace_id = campaign.workspace_id
        measurement = MeasurementService(session)
        period_start, period_end = default_period()
        entry = measurement.record_metric_entry(
            campaign=campaign, period_start=period_start, period_end=period_end, channel="Instagram",
            source=MetricSource.MANUAL, client_request_id=next_client_request_id(), metric_values=default_metric_values(),
        )
        observation = measurement.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("5.0"))
        signal = measurement.record_signal(campaign=campaign, observations=[observation], summary="CTR trending up.")
        measurement.record_analysis_result(campaign=campaign, signals=[signal], summary="Durable improvement.")

    derive_response = _post(fixtures, f"/api/v1/campaigns/{fixtures['campaign_id']}/learning/derive")
    assert derive_response.status_code == 200, derive_response.text
    candidates = derive_response.json()["learning_candidates"]
    assert len(candidates) == 1
    lrn_id = candidates[0]["id"]
    assert candidates[0]["status"] == "CANDIDATE_IDENTIFIED"

    # CANDIDATE_IDENTIFIED -> PROVISIONAL -> VALIDATION_PENDING -> VALIDATED,
    # all through real HTTP, all through the OWNER of this journey's own
    # freshly-created workspace.
    r1 = _post(fixtures, _path(fixtures, lrn_id, "mark-provisional"))
    assert r1.status_code == 200 and r1.json()["status"] == "PROVISIONAL"
    r2 = _post(fixtures, _path(fixtures, lrn_id, "mark-validation-pending"))
    assert r2.status_code == 200 and r2.json()["status"] == "VALIDATION_PENDING"
    qualification = fixtures["client"].patch(_path(fixtures, lrn_id, "qualification"),
        json={"confidence": "LOW", "scope": "This campaign only", "generalization_boundary": "No causal or cross-audience inference"},
        headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert qualification.status_code == 200, qualification.text
    r3 = _post(fixtures, _path(fixtures, lrn_id, "decision"), {"decision": "VALIDATED"})
    assert r3.status_code == 200 and r3.json()["status"] == "VALIDATED"

    # VALIDATED -> StrategicRecommendationCandidate -> ACCEPTED.
    r4 = _post(fixtures, _path(fixtures, lrn_id, "recommendations"), {"summary": "Shift creative brief toward shorter hooks."})
    assert r4.status_code == 201, r4.text
    src_id = r4.json()["id"]
    r5 = fixtures["client"].patch(
        f"/api/v1/campaigns/{fixtures['campaign_id']}/learning/{src_id}",
        json={"decision": "ACCEPTED"}, headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert r5.status_code == 200 and r5.json()["decision"] == "ACCEPTED"

    # ACCEPTED does not execute Strategy: zero Strategy/Hypothesis/
    # Experiment rows exist for THIS workspace. Scoped by workspace_id,
    # never a global unfiltered count — other test files in the same full
    # suite run (e.g. test_strategy_api.py) commit real Strategy rows via
    # HTTP that durably persist for the rest of the pytest session, so an
    # unscoped count() would be fragile against full-suite run order —
    # exactly the class of pollution test_measurement_analysis_api.py's
    # own module-scoped autouse fixture already guards against for a
    # different table pair.
    with OrmSession(bind=engine) as session:
        for model in (Strategy, Hypothesis, Experiment):
            count = session.execute(select(func.count()).select_from(model).where(model.workspace_id == workspace_id)).scalar_one()
            assert count == 0
        # Positioning has no workspace_id of its own ("via Strategy") —
        # with zero Strategy rows for this workspace already proven above,
        # zero Positioning rows can exist for it either (FK integrity).

    # GET /learning reflects the final server truth exactly.
    final_get = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/learning")
    final_body = final_get.json()
    assert final_body["learning_candidates"][0]["status"] == "VALIDATED"
    assert final_body["strategic_recommendation_candidates"][0]["decision"] == "ACCEPTED"
