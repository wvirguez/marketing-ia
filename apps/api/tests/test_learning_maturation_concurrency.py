"""Real PostgreSQL two-connection races through the production
LearningService, proving MVP-23B's uniform ``for_update=True`` locking
rule (MVP-23B §8/§9): every transition/decision must be evaluated against
the row loaded under lock, never a pre-lock read. Mirrors
``tests/test_distribution_concurrency.py``'s own ``_race`` helper shape
exactly — a ``threading.Barrier`` ensures both threads attempt their
operation nearly simultaneously; PostgreSQL's own row lock, not a Python
mock, is what actually serializes them.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.campaigns.repository import CampaignRepository
from app.core.api_errors import InvalidLifecycleTransitionError, RecommendationAlreadyDecidedError
from app.learning.models import LearningCandidateStatus, StrategicRecommendationDecision
from app.learning.service import LearningService
from tests.learningtest import seed_sufficient_qualification
from app.measurement.models import MetricSource
from app.measurement.service import MeasurementService
from tests.contenttest import make_user
from tests.measurementtest import default_metric_values, default_period, next_client_request_id
from tests.researchtest import build_campaign_run_with_stages

pytestmark = pytest.mark.postgres


def _candidate_at(engine, target_status: LearningCandidateStatus):
    """Builds one AnalysisResult -> LearningCandidate and drives it to
    exactly ``target_status``, committing on the given engine. Returns
    ``(campaign_id, learning_candidate_id, workspace_id)``."""
    with Session(engine) as session:
        campaign, _run, _stages = build_campaign_run_with_stages(session)
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
        candidate = learning.record_learning_candidate(analysis_result=analysis_result, summary="Race fixture.")
        seed_sufficient_qualification(session, candidate)
        path = {
            LearningCandidateStatus.VALIDATION_PENDING: [LearningCandidateStatus.PROVISIONAL, LearningCandidateStatus.VALIDATION_PENDING],
            LearningCandidateStatus.INSUFFICIENT_EVIDENCE: [
                LearningCandidateStatus.PROVISIONAL, LearningCandidateStatus.VALIDATION_PENDING, LearningCandidateStatus.INSUFFICIENT_EVIDENCE,
            ],
            LearningCandidateStatus.VALIDATED: [
                LearningCandidateStatus.PROVISIONAL, LearningCandidateStatus.VALIDATION_PENDING, LearningCandidateStatus.VALIDATED,
            ],
        }[target_status]
        for step in path:
            candidate = learning.transition_learning_candidate(learning_candidate=candidate, target_status=step)
        return campaign.public_id, candidate.public_id, campaign.workspace_id


def _run_two(engine, operation) -> tuple[list[str], list[BaseException], list[int]]:
    """Runs ``operation(session, which) -> "ok" | "conflict"`` on two
    genuinely separate connections bound to ``engine``, synchronized to
    overlap via a Barrier."""
    outcomes: list[str] = []
    errors: list[BaseException] = []
    backend_pids: list[int] = []
    pid_lock = threading.Lock()

    def verify_distinct_connections():
        assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1], backend_pids

    barrier = threading.Barrier(2, action=verify_distinct_connections)

    def run(which: int):
        try:
            with engine.connect() as connection:
                pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
                connection.commit()
                with pid_lock:
                    backend_pids.append(pid)
                with Session(bind=connection) as session:
                    barrier.wait(timeout=10)
                    try:
                        operation(session, which)
                        outcomes.append("ok")
                    except (InvalidLifecycleTransitionError, RecommendationAlreadyDecidedError):
                        outcomes.append("conflict")
        except BaseException as exc:  # noqa: BLE001 - surfaced via assertion below
            errors.append(exc)

    threads = [threading.Thread(target=lambda i=i: run(i)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not errors, errors
    assert all(not thread.is_alive() for thread in threads)
    return outcomes, errors, backend_pids


def test_learning_decision_race_validated_vs_rejected(postgres_engine):
    """MVP-23B §36: VALIDATION_PENDING -> {VALIDATED, REJECTED} raced by
    two genuinely separate sessions through the same locked lookup +
    transition_learning_candidate. Exactly one must succeed; the other
    must deterministically 409; final state must equal the winner's
    target; exactly one final-transition audit event must exist."""
    campaign_id, candidate_public_id, _workspace_id = _candidate_at(postgres_engine, LearningCandidateStatus.VALIDATION_PENDING)
    targets = {0: LearningCandidateStatus.VALIDATED, 1: LearningCandidateStatus.REJECTED}

    def operation(session: Session, which: int) -> None:
        campaign = CampaignRepository(session).get_by_public_id(campaign_id)
        service = LearningService(session)
        candidate = service.candidates.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=candidate_public_id, for_update=True
        )
        service.transition_learning_candidate(learning_candidate=candidate, target_status=targets[which])

    outcomes, _errors, backend_pids = _run_two(postgres_engine, operation)
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 1 and outcomes.count("conflict") == 1, outcomes

    with Session(postgres_engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_id)
        service = LearningService(session)
        final = service.candidates.get_for_campaign_by_public_id(campaign_id=campaign.id, public_id=candidate_public_id)
        assert final.status in (LearningCandidateStatus.VALIDATED, LearningCandidateStatus.REJECTED)

        events = session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "learning.candidate.status_changed",
                AuditEvent.learning_candidate_id == final.id,
                AuditEvent.new_state.in_(["VALIDATED", "REJECTED"]),
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].new_state == final.status.value


def test_reopen_race_double_mark_validation_pending(postgres_engine):
    """MVP-23B §37: two humans race to reopen the SAME INSUFFICIENT_EVIDENCE
    candidate simultaneously. VALIDATION_PENDING has no legal self-loop, so
    the second (post-lock) caller must deterministically 409 rather than
    silently double-fire the reopen. This proves the reopen edge (MVP-23A-
    R1) goes through the identical locking discipline as every other edge
    — a real, meaningful race, not an invented one."""
    campaign_id, candidate_public_id, _workspace_id = _candidate_at(postgres_engine, LearningCandidateStatus.INSUFFICIENT_EVIDENCE)

    def operation(session: Session, which: int) -> None:
        campaign = CampaignRepository(session).get_by_public_id(campaign_id)
        service = LearningService(session)
        candidate = service.candidates.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=candidate_public_id, for_update=True
        )
        service.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.VALIDATION_PENDING)

    outcomes, _errors, backend_pids = _run_two(postgres_engine, operation)
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 1 and outcomes.count("conflict") == 1, outcomes

    with Session(postgres_engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_id)
        service = LearningService(session)
        final = service.candidates.get_for_campaign_by_public_id(campaign_id=campaign.id, public_id=candidate_public_id)
        assert final.status is LearningCandidateStatus.VALIDATION_PENDING

        reopen_events = session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "learning.candidate.status_changed",
                AuditEvent.learning_candidate_id == final.id,
                AuditEvent.previous_state == "INSUFFICIENT_EVIDENCE",
            )
        ).scalars().all()
        assert len(reopen_events) == 1


def test_recommendation_decision_race_accepted_vs_rejected(postgres_engine):
    """MVP-23B §38: the same ACCEPTED-vs-REJECTED race MVP-22-era tests
    already proved sequentially, now proved with genuinely separate
    connections. Exactly one succeeds; the other raises
    RecommendationAlreadyDecidedError; final decision equals the winner;
    exactly one decision audit event exists."""
    campaign_id, candidate_public_id, _workspace_id = _candidate_at(postgres_engine, LearningCandidateStatus.VALIDATED)
    with Session(postgres_engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_id)
        service = LearningService(session)
        candidate = service.candidates.get_for_campaign_by_public_id(campaign_id=campaign.id, public_id=candidate_public_id)
        implication = service.record_strategic_implication(learning_candidate=candidate, statement="Race fixture implication.")
        recommendation = service.record_strategic_recommendation_candidate(
            campaign=campaign, learning_candidate=candidate, strategic_implication_public_id=implication.public_id,
            summary="Race fixture recommendation.",
        )
        recommendation_public_id = recommendation.public_id
        user = make_user(session)
        session.commit()
        user_id = user.id

    decisions = {0: StrategicRecommendationDecision.ACCEPTED, 1: StrategicRecommendationDecision.REJECTED}

    def operation(session: Session, which: int) -> None:
        campaign = CampaignRepository(session).get_by_public_id(campaign_id)
        service = LearningService(session)
        service.decide_strategic_recommendation_candidate(
            campaign=campaign, recommendation_public_id=recommendation_public_id,
            decision=decisions[which], actor_user_id=user_id,
        )

    outcomes, _errors, backend_pids = _run_two(postgres_engine, operation)
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 1 and outcomes.count("conflict") == 1, outcomes

    with Session(postgres_engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_id)
        service = LearningService(session)
        final = service.recommendations.get_for_campaign_by_public_id(campaign_id=campaign.id, public_id=recommendation_public_id)
        assert final.decision in (StrategicRecommendationDecision.ACCEPTED, StrategicRecommendationDecision.REJECTED)

        events = session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "learning.recommendation.decided",
                AuditEvent.strategic_recommendation_candidate_id == final.id,
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].new_state == final.decision.value


def test_recommendation_creation_race_both_succeed(postgres_engine):
    """MVP-23B §39: cardinality is deliberately 0..N, so two simultaneous
    recommendation creations for the same VALIDATED candidate must BOTH
    succeed as two distinct rows — proving this is not accidentally
    serialized/blocked by any lock (there is none, by design, MVP-23A §AK)."""
    campaign_id, candidate_public_id, _workspace_id = _candidate_at(postgres_engine, LearningCandidateStatus.VALIDATED)
    with Session(postgres_engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_id)
        candidate = LearningService(session).candidates.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=candidate_public_id
        )
        implication = LearningService(session).record_strategic_implication(
            learning_candidate=candidate, statement="Shared race fixture implication."
        )
        implication_public_id = implication.public_id
    created_ids: list[str] = []
    ids_lock = threading.Lock()

    def operation(session: Session, which: int) -> None:
        campaign = CampaignRepository(session).get_by_public_id(campaign_id)
        service = LearningService(session)
        candidate = service.candidates.get_for_campaign_by_public_id(campaign_id=campaign.id, public_id=candidate_public_id)
        recommendation = service.record_strategic_recommendation_candidate(
            campaign=campaign, learning_candidate=candidate, strategic_implication_public_id=implication_public_id,
            summary=f"Concurrent option {which}.",
        )
        with ids_lock:
            created_ids.append(recommendation.public_id)

    outcomes, _errors, backend_pids = _run_two(postgres_engine, operation)
    assert len(backend_pids) == 2 and backend_pids[0] != backend_pids[1]
    assert outcomes.count("ok") == 2, outcomes
    assert len(created_ids) == 2 and created_ids[0] != created_ids[1]
