"""Measurement -> Learning bridge tests (MVP-12B-A/-R1/-R2). All marked
`postgres`.

Covers the explicit, deterministic bridge that derives (or safely reuses)
a LearningCandidate from an already-persisted AnalysisResult, its
concurrency-safe LearningDerivation provenance, its audit attribution, and
the public POST /campaigns/{id}/learning/derive endpoint. No public write
endpoint exists for arbitrary LearningCandidate content — only this
narrow, qualification-free derivation path.
"""

from __future__ import annotations

import threading
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from app.audit.models import AuditEvent, ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.campaigns.repository import CampaignRepository
from app.learning.models import LearningCandidate, LearningCandidateStatus, LearningDerivation, StrategicRecommendationCandidate
from app.learning.service import EVENT_CANDIDATE_RECORDED, LearningService
from app.measurement.models import AnalysisResult, MetricSource
from app.measurement.service import MeasurementService
from app.persistence.session import get_engine
from app.workspaces.models import Membership, MembershipStatus
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.learningtest import build_analysis_result, build_learning_candidate
from tests.measurementtest import default_metric_values, default_period, next_client_request_id

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def _candidate_count_for_analysis_result(session, analysis_result_id) -> int:
    """Scoped to one AnalysisResult, never a bare table-wide count — this
    suite shares a real, session-scoped Postgres database with every other
    Learning/Measurement test file, so an absolute total row count would
    be contaminated by unrelated tests' own permanently-committed rows."""
    return session.execute(
        select(func.count()).select_from(LearningCandidate).where(LearningCandidate.analysis_result_id == analysis_result_id)
    ).scalar_one()


def _seed_analysis_result(campaign_public_id: str) -> None:
    """Records one MetricEntry -> Observation -> Signal -> AnalysisResult
    for an already-created Campaign, via a genuinely separate,
    immediately-committed session bound to the app's own engine — the
    same pattern `tests/test_learning_api.py::_record_recommendation`
    uses."""
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
        measurement.record_analysis_result(campaign=campaign, signals=[signal], summary="CTR improvement is durable.")


def _active_member_user_id(campaign_public_id: str) -> uuid.UUID:
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_public_id)
        membership = session.execute(
            select(Membership).where(
                Membership.workspace_id == campaign.workspace_id, Membership.status == MembershipStatus.ACTIVE
            )
        ).scalars().first()
        return membership.user_id


def _derive_path(fixtures: dict) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/learning/derive"


# --- basic derivation ------------------------------------------------------


def test_first_derive_creates_one_bridge_candidate(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    service = LearningService(db_session)

    candidate, created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)

    assert created is True
    derivation = service.derivations.get_by_analysis_result_id(analysis_result.id)
    assert derivation is not None
    assert derivation.learning_candidate_id == candidate.id


def test_sequential_replay_creates_no_new_candidate(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    service = LearningService(db_session)

    first_candidate, first_created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)
    second_candidate, second_created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)

    assert first_created is True
    assert second_created is False
    assert second_candidate.id == first_candidate.id
    assert _candidate_count_for_analysis_result(db_session, analysis_result.id) == 1


# --- non-bridge candidate coexistence --------------------------------------


def test_preexisting_manual_candidate_does_not_suppress_bridge_derivation(db_session) -> None:
    _campaign, _analysis_result, manual_candidate = build_learning_candidate(db_session)
    analysis_result = db_session.get(AnalysisResult, manual_candidate.analysis_result_id)
    service = LearningService(db_session)

    bridge_candidate, created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)

    assert created is True
    assert bridge_candidate.id != manual_candidate.id
    assert _candidate_count_for_analysis_result(db_session, analysis_result.id) == 2
    assert service.derivations.get_by_learning_candidate_id(manual_candidate.id) is None
    assert service.derivations.get_by_learning_candidate_id(bridge_candidate.id) is not None


def test_second_derive_after_manual_and_bridge_candidates_creates_no_third(db_session) -> None:
    _campaign, _analysis_result, manual_candidate = build_learning_candidate(db_session)
    analysis_result = db_session.get(AnalysisResult, manual_candidate.analysis_result_id)
    service = LearningService(db_session)

    bridge_candidate, _created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)
    again_candidate, again_created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)

    assert again_created is False
    assert again_candidate.id == bridge_candidate.id
    assert _candidate_count_for_analysis_result(db_session, analysis_result.id) == 2


# --- content / provenance fidelity -----------------------------------------


def test_bridge_candidate_summary_matches_analysis_result_summary_exactly(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    service = LearningService(db_session)

    candidate, _created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)

    assert candidate.summary == analysis_result.summary


def test_learning_derivation_references_exact_analysis_result_and_candidate(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    service = LearningService(db_session)

    candidate, _created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)
    derivation = service.derivations.get_by_analysis_result_id(analysis_result.id)

    assert derivation.analysis_result_id == analysis_result.id
    assert derivation.learning_candidate_id == candidate.id
    assert derivation.workspace_id == analysis_result.workspace_id


def test_bridge_candidate_workspace_matches_analysis_result_workspace(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    service = LearningService(db_session)

    candidate, _created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)

    assert candidate.workspace_id == analysis_result.workspace_id


def test_bridge_candidate_starts_candidate_identified(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    service = LearningService(db_session)

    candidate, _created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)

    assert candidate.status is LearningCandidateStatus.CANDIDATE_IDENTIFIED


# --- atomicity / integrity error handling -----------------------------------


def test_failed_derivation_insert_leaves_no_orphan_candidate(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    service = LearningService(db_session)

    # Simulate "another winner already claimed this AnalysisResult" by
    # seeding the derivation directly via the same low-level primitives.
    seeded_candidate = service.candidates.create(analysis_result=analysis_result, summary="seed")
    service.derivations.create(analysis_result=analysis_result, learning_candidate=seeded_candidate)
    db_session.commit()
    candidates_before = _total_count(db_session, LearningCandidate)

    candidate, created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)

    assert created is False
    assert candidate.id == seeded_candidate.id
    assert _total_count(db_session, LearningCandidate) == candidates_before, "no orphan candidate may survive the recovery path"


def test_unexpected_integrity_error_is_reraised_not_swallowed(db_session, monkeypatch) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    service = LearningService(db_session)

    def _boom(*args, **kwargs):
        raise IntegrityError("statement", {}, Exception("unrelated constraint violation"))

    monkeypatch.setattr(service.derivations, "create", _boom)

    with pytest.raises(IntegrityError):
        service.derive_candidate_from_analysis_result(analysis_result=analysis_result)


def test_audit_failure_rolls_back_bridge_candidate_and_derivation(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    candidates_before = _total_count(db_session, LearningCandidate)
    derivations_before = _total_count(db_session, LearningDerivation)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            LearningService(db_session).derive_candidate_from_analysis_result(analysis_result=analysis_result)

    db_session.rollback()
    assert _total_count(db_session, LearningCandidate) == candidates_before
    assert _total_count(db_session, LearningDerivation) == derivations_before


# --- audit content -----------------------------------------------------


def test_replay_creates_no_duplicate_candidate_recorded_audit_event(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    service = LearningService(db_session)

    candidate, _created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)
    service.derive_candidate_from_analysis_result(analysis_result=analysis_result)

    events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == EVENT_CANDIDATE_RECORDED, AuditEvent.learning_candidate_id == candidate.id
        )
    ).scalars().all()
    assert len(events) == 1


def test_candidate_recorded_audit_references_exact_bridge_candidate(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    service = LearningService(db_session)

    candidate, _created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)

    event = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_CANDIDATE_RECORDED, AuditEvent.learning_candidate_id == candidate.id)
    ).scalar_one()
    assert event.learning_candidate_id == candidate.id


def test_audit_workspace_matches_candidate_and_analysis_result_workspace(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    service = LearningService(db_session)

    candidate, _created = service.derive_candidate_from_analysis_result(analysis_result=analysis_result)

    event = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_CANDIDATE_RECORDED, AuditEvent.learning_candidate_id == candidate.id)
    ).scalar_one()
    assert event.workspace_id == candidate.workspace_id == analysis_result.workspace_id


# --- governance boundaries ---------------------------------------------


def test_derive_creates_no_strategic_recommendation_candidate(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    service = LearningService(db_session)
    before = _total_count(db_session, StrategicRecommendationCandidate)

    service.derive_candidate_from_analysis_result(analysis_result=analysis_result)

    assert _total_count(db_session, StrategicRecommendationCandidate) == before


def test_strategy_tables_unchanged_by_derive(db_session) -> None:
    from app.strategy.models import Experiment, Hypothesis, Positioning, Strategy

    _campaign, analysis_result = build_analysis_result(db_session)
    counts_before = {model: _total_count(db_session, model) for model in (Strategy, Positioning, Hypothesis, Experiment)}

    LearningService(db_session).derive_candidate_from_analysis_result(analysis_result=analysis_result)

    for model, before in counts_before.items():
        assert _total_count(db_session, model) == before, f"{model.__name__} must be untouched by the bridge"


def test_orchestration_tables_unchanged_by_derive(db_session) -> None:
    from app.orchestration.models import HumanDecisionRequest, HumanDecisionResponse, RunStageExecution

    _campaign, analysis_result = build_analysis_result(db_session)
    counts_before = {
        model: _total_count(db_session, model) for model in (RunStageExecution, HumanDecisionRequest, HumanDecisionResponse)
    }

    LearningService(db_session).derive_candidate_from_analysis_result(analysis_result=analysis_result)

    for model, before in counts_before.items():
        assert _total_count(db_session, model) == before, f"{model.__name__} must be untouched by the bridge"


# --- real PostgreSQL concurrency (mandatory) --------------------------------


def test_concurrent_derive_requests_create_exactly_one_bridge_candidate(postgres_engine) -> None:
    """Two independent connections/sessions race to derive a candidate for
    the SAME AnalysisResult. Exactly one LearningDerivation, one
    bridge-created LearningCandidate, and one candidate-recorded audit
    event may survive — both callers must resolve without error, and the
    losing attempt must leave no orphan row of any kind."""
    with postgres_engine.connect() as setup_connection:
        setup_session = OrmSession(bind=setup_connection)
        _campaign, analysis_result = build_analysis_result(setup_session, campaign_name="Bridge Race Campaign")
        setup_session.commit()
        analysis_result_id = analysis_result.id
        setup_session.close()

    ready = threading.Barrier(2)
    results: list[tuple[uuid.UUID, bool]] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def attempt() -> None:
        with postgres_engine.connect() as connection:
            session = OrmSession(bind=connection)
            try:
                local_analysis_result = session.get(AnalysisResult, analysis_result_id)
                service = LearningService(session)
                ready.wait(timeout=5)
                candidate, created = service.derive_candidate_from_analysis_result(analysis_result=local_analysis_result)
                with lock:
                    results.append((candidate.id, created))
            except Exception as exc:  # pragma: no cover - surfaced via errors list
                with lock:
                    errors.append(exc)
            finally:
                session.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"unexpected errors: {errors}"
    assert len(results) == 2
    candidate_ids = {candidate_id for candidate_id, _created in results}
    assert len(candidate_ids) == 1, "both concurrent callers must observe the same winning candidate"
    created_flags = sorted(created for _candidate_id, created in results)
    assert created_flags == [False, True], "exactly one attempt creates; the other safely observes the existing row"

    with postgres_engine.connect() as connection:
        derivation_rows = connection.execute(
            select(LearningDerivation.__table__).where(LearningDerivation.__table__.c.analysis_result_id == analysis_result_id)
        ).all()
        assert len(derivation_rows) == 1, "exactly one LearningDerivation may survive the race"

        candidate_rows = connection.execute(
            select(LearningCandidate.__table__).where(LearningCandidate.__table__.c.analysis_result_id == analysis_result_id)
        ).all()
        assert len(candidate_rows) == 1, "the losing attempt must leave no orphan LearningCandidate"

        winner_candidate_id = derivation_rows[0].learning_candidate_id
        recorded_events = connection.execute(
            select(AuditEvent.__table__).where(
                AuditEvent.__table__.c.event_type == EVENT_CANDIDATE_RECORDED,
                AuditEvent.__table__.c.learning_candidate_id == winner_candidate_id,
            )
        ).all()
        assert len(recorded_events) == 1, "exactly one candidate-recorded audit event may exist — the loser's must never survive"


# --- router / HTTP contract ------------------------------------------------


def test_derive_only_processes_analysis_results_of_url_campaign(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _seed_analysis_result(fixtures["campaign_id"])

    client_b = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    csrf_b = register_and_get_csrf(client_b, display_name="Other User")
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="Other Campaign"), headers={"X-CSRF-Token": csrf_b}).json()
    _seed_analysis_result(body_b["campaign"]["id"])

    response = fixtures["client"].post(_derive_path(fixtures), headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert response.status_code == 200
    body = response.json()
    assert len(body["learning_candidates"]) == 1

    other_response = client_b.post(
        f"/api/v1/campaigns/{body_b['campaign']['id']}/learning/derive", headers={"X-CSRF-Token": csrf_b}
    )
    assert other_response.status_code == 200
    other_body = other_response.json()
    assert len(other_body["learning_candidates"]) == 1
    assert other_body["learning_candidates"][0]["id"] != body["learning_candidates"][0]["id"]


def test_get_learning_includes_bridge_created_candidate_after_derive(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _seed_analysis_result(fixtures["campaign_id"])

    derive_response = fixtures["client"].post(_derive_path(fixtures), headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert derive_response.status_code == 200
    derived_id = derive_response.json()["learning_candidates"][0]["id"]

    get_response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/learning")
    assert get_response.status_code == 200
    candidate_ids = [c["id"] for c in get_response.json()["learning_candidates"]]
    assert derived_id in candidate_ids


def test_derive_response_exposes_no_raw_uuid(campaign_run_client: dict) -> None:
    import re

    fixtures = campaign_run_client
    _seed_analysis_result(fixtures["campaign_id"])

    response = fixtures["client"].post(_derive_path(fixtures), headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert response.status_code == 200
    uuid_pattern = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    assert uuid_pattern.search(response.text) is None


def test_explicit_http_derive_records_actor_type_user(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _seed_analysis_result(fixtures["campaign_id"])

    response = fixtures["client"].post(_derive_path(fixtures), headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert response.status_code == 200
    candidate_public_id = response.json()["learning_candidates"][0]["id"]

    engine = get_engine()
    with OrmSession(bind=engine) as session:
        candidate = session.execute(
            select(LearningCandidate).where(LearningCandidate.public_id == candidate_public_id)
        ).scalar_one()
        event = session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == EVENT_CANDIDATE_RECORDED, AuditEvent.learning_candidate_id == candidate.id
            )
        ).scalar_one()
        assert event.actor_type is ActorType.USER


def test_explicit_http_derive_records_triggering_users_id(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _seed_analysis_result(fixtures["campaign_id"])
    expected_user_id = _active_member_user_id(fixtures["campaign_id"])

    response = fixtures["client"].post(_derive_path(fixtures), headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert response.status_code == 200
    candidate_public_id = response.json()["learning_candidates"][0]["id"]

    engine = get_engine()
    with OrmSession(bind=engine) as session:
        candidate = session.execute(
            select(LearningCandidate).where(LearningCandidate.public_id == candidate_public_id)
        ).scalar_one()
        event = session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == EVENT_CANDIDATE_RECORDED, AuditEvent.learning_candidate_id == candidate.id
            )
        ).scalar_one()
        assert event.actor_user_id == expected_user_id


def test_derive_on_campaign_with_zero_analysis_results_returns_200_noop(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client

    response = fixtures["client"].post(_derive_path(fixtures), headers={"X-CSRF-Token": fixtures["csrf_token"]})

    assert response.status_code == 200
    assert response.json() == {"learning_candidates": [], "strategic_recommendation_candidates": []}


def test_wrong_workspace_campaign_derive_is_forbidden_and_creates_nothing(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    csrf_b = register_and_get_csrf(client_b, display_name="User B")
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    _seed_analysis_result(body_b["campaign"]["id"])

    candidates_before = 0
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        candidates_before = _total_count(session, LearningCandidate)

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    response = client_a.post(
        f"/api/v1/campaigns/{body_b['campaign']['id']}/learning/derive", headers={"X-CSRF-Token": csrf_a}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    with OrmSession(bind=engine) as session:
        assert _total_count(session, LearningCandidate) == candidates_before
