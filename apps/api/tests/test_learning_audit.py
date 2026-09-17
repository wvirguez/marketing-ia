"""Audit attribution and atomicity for Learning persistence (BACKEND-14
Governance Freeze §V). All marked `postgres`.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.core.api_errors import RecommendationAlreadyDecidedError
from app.learning.models import LearningCandidate, LearningCandidateStatus, StrategicRecommendationCandidate, StrategicRecommendationDecision
from app.learning.service import LearningService
from tests.contenttest import make_user
from tests.learningtest import (
    build_analysis_result,
    build_learning_candidate,
    build_recommendation,
    build_strategic_implication,
    build_validated_learning_candidate,
)

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- exact attribution ------------------------------------------------


def test_candidate_recorded_event_identifies_the_exact_candidate(db_session) -> None:
    _campaign, _analysis_result, candidate = build_learning_candidate(db_session)
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "learning.candidate.recorded")
    ).scalars().all()
    matching = [e for e in events if e.learning_candidate_id == candidate.id]
    assert len(matching) == 1


def test_status_changed_event_carries_previous_and_new_state(db_session) -> None:
    _campaign, _analysis_result, candidate = build_learning_candidate(db_session)
    LearningService(db_session).transition_learning_candidate(
        learning_candidate=candidate, target_status=LearningCandidateStatus.PROVISIONAL
    )
    events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "learning.candidate.status_changed", AuditEvent.learning_candidate_id == candidate.id
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].previous_state == "CANDIDATE_IDENTIFIED"
    assert events[0].new_state == "PROVISIONAL"


def test_two_candidates_recorded_close_together_are_never_confused(db_session) -> None:
    _campaign1, analysis_result1 = build_analysis_result(db_session, campaign_name="Campaign One")
    _campaign2, analysis_result2 = build_analysis_result(db_session, campaign_name="Campaign Two")
    service = LearningService(db_session)
    candidate_a = service.record_learning_candidate(analysis_result=analysis_result1, summary="A")
    candidate_b = service.record_learning_candidate(analysis_result=analysis_result2, summary="B")

    events = db_session.execute(select(AuditEvent).where(AuditEvent.event_type == "learning.candidate.recorded")).scalars().all()
    matching_a = [e for e in events if e.learning_candidate_id == candidate_a.id]
    matching_b = [e for e in events if e.learning_candidate_id == candidate_b.id]
    assert len(matching_a) == 1
    assert len(matching_b) == 1
    assert matching_a[0].id != matching_b[0].id


def test_recommendation_recorded_event_identifies_exact_rows(db_session) -> None:
    _campaign, _analysis_result, candidate, recommendation = build_recommendation(db_session)
    events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "learning.recommendation.recorded",
            AuditEvent.strategic_recommendation_candidate_id == recommendation.id,
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].learning_candidate_id == candidate.id
    # MVP-26: the recommendation audit event also carries the
    # StrategicImplication FK it was created from, proving the new-write
    # bridge, never inferred positionally.
    assert events[0].strategic_implication_id == recommendation.strategic_implication_id
    assert events[0].strategic_implication_id is not None


def test_strategic_implication_recorded_event_identifies_exact_row(db_session) -> None:
    """MVP-26 §29/§39: creation is attributed to ActorType.USER when an
    actor_user_id is supplied, and the event names the exact Implication
    and its parent Learning Candidate — never inferred positionally."""
    _campaign, _analysis_result, candidate, implication = build_strategic_implication(db_session)
    user = make_user(db_session)
    db_session.commit()
    LearningService(db_session).record_strategic_implication(
        learning_candidate=candidate, statement="Second angle.", actor_user_id=user.id,
    )
    events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "learning.strategic_implication.recorded",
            AuditEvent.learning_candidate_id == candidate.id,
        )
    ).scalars().all()
    assert len(events) == 2  # one from build_strategic_implication (SYSTEM), one above (USER)
    system_event = next(e for e in events if e.actor_user_id is None)
    user_event = next(e for e in events if e.actor_user_id == user.id)
    assert system_event.actor_type is ActorType.SYSTEM
    assert user_event.actor_type is ActorType.USER
    assert system_event.strategic_implication_id == implication.id
    assert user_event.strategic_implication_id is not None and user_event.strategic_implication_id != implication.id


def test_recommendation_decided_event_carries_new_state(db_session) -> None:
    campaign, _analysis_result, _candidate, recommendation = build_recommendation(db_session)
    reviewer = make_user(db_session)
    LearningService(db_session).decide_strategic_recommendation_candidate(
        campaign=campaign, recommendation_public_id=recommendation.public_id,
        decision=StrategicRecommendationDecision.ACCEPTED, actor_user_id=reviewer.id,
    )
    events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "learning.recommendation.decided",
            AuditEvent.strategic_recommendation_candidate_id == recommendation.id,
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].new_state == "ACCEPTED"
    assert events[0].actor_user_id == reviewer.id


def test_no_duplicate_decision_event_on_second_decision_attempt(db_session) -> None:
    campaign, _analysis_result, _candidate, recommendation = build_recommendation(db_session)
    reviewer = make_user(db_session)
    service = LearningService(db_session)
    service.decide_strategic_recommendation_candidate(
        campaign=campaign, recommendation_public_id=recommendation.public_id,
        decision=StrategicRecommendationDecision.ACCEPTED, actor_user_id=reviewer.id,
    )
    with pytest.raises(RecommendationAlreadyDecidedError):
        service.decide_strategic_recommendation_candidate(
            campaign=campaign, recommendation_public_id=recommendation.public_id,
            decision=StrategicRecommendationDecision.REJECTED, actor_user_id=reviewer.id,
        )
    events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "learning.recommendation.decided",
            AuditEvent.strategic_recommendation_candidate_id == recommendation.id,
        )
    ).scalars().all()
    assert len(events) == 1
    db_session.refresh(recommendation)
    assert recommendation.decision is StrategicRecommendationDecision.ACCEPTED


def test_reversal_is_forbidden(db_session) -> None:
    campaign, _analysis_result, _candidate, recommendation = build_recommendation(db_session)
    reviewer = make_user(db_session)
    service = LearningService(db_session)
    service.decide_strategic_recommendation_candidate(
        campaign=campaign, recommendation_public_id=recommendation.public_id,
        decision=StrategicRecommendationDecision.REJECTED, actor_user_id=reviewer.id,
    )
    with pytest.raises(RecommendationAlreadyDecidedError):
        service.decide_strategic_recommendation_candidate(
            campaign=campaign, recommendation_public_id=recommendation.public_id,
            decision=StrategicRecommendationDecision.ACCEPTED, actor_user_id=reviewer.id,
        )
    db_session.refresh(recommendation)
    assert recommendation.decision is StrategicRecommendationDecision.REJECTED


# --- atomicity: rollback on failure --------------------------------------


def test_audit_failure_rolls_back_the_learning_candidate(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    candidates_before = _total_count(db_session, LearningCandidate)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            LearningService(db_session).record_learning_candidate(analysis_result=analysis_result, summary="x")

    db_session.rollback()
    assert _total_count(db_session, LearningCandidate) == candidates_before


def test_audit_failure_rolls_back_a_transition(db_session) -> None:
    _campaign, _analysis_result, candidate = build_learning_candidate(db_session)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            LearningService(db_session).transition_learning_candidate(
                learning_candidate=candidate, target_status=LearningCandidateStatus.PROVISIONAL
            )

    db_session.rollback()
    db_session.refresh(candidate)
    assert candidate.status is LearningCandidateStatus.CANDIDATE_IDENTIFIED


def test_audit_failure_rolls_back_the_recommendation(db_session) -> None:
    campaign, _analysis_result, candidate = build_validated_learning_candidate(db_session)
    implication = LearningService(db_session).record_strategic_implication(learning_candidate=candidate, statement="x")
    recommendations_before = _total_count(db_session, StrategicRecommendationCandidate)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            LearningService(db_session).record_strategic_recommendation_candidate(
                campaign=campaign, learning_candidate=candidate, strategic_implication_public_id=implication.public_id, summary="x"
            )

    db_session.rollback()
    assert _total_count(db_session, StrategicRecommendationCandidate) == recommendations_before


def test_audit_failure_rolls_back_the_decision(db_session) -> None:
    campaign, _analysis_result, _candidate, recommendation = build_recommendation(db_session)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            LearningService(db_session).decide_strategic_recommendation_candidate(
                campaign=campaign, recommendation_public_id=recommendation.public_id,
                decision=StrategicRecommendationDecision.ACCEPTED, actor_user_id=uuid.uuid4(),
            )

    db_session.rollback()
    db_session.refresh(recommendation)
    assert recommendation.decision is None
    assert recommendation.decided_at is None
