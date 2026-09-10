"""Learning domain persistence, tenancy, lifecycle, and governance-boundary
tests (BACKEND-14). All marked `postgres`.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.api_errors import InvalidLifecycleTransitionError, LearningCandidateNotValidatedError
from app.learning.models import LearningCandidate, LearningCandidateStatus, StrategicRecommendationCandidate, StrategicRecommendationDecision
from app.learning.service import LearningService
from app.learning.transitions import LEARNING_CANDIDATE_TRANSITIONS, is_legal_learning_candidate_transition
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.learningtest import build_analysis_result, build_learning_candidate, build_recommendation, build_validated_learning_candidate

pytestmark = pytest.mark.postgres


# --- LearningCandidate: domain persistence -------------------------------


def test_learning_candidate_persists_with_correct_workspace_and_initial_status(db_session) -> None:
    campaign, analysis_result, candidate = build_learning_candidate(db_session)
    assert candidate.public_id.startswith("LRN-")
    assert candidate.workspace_id == campaign.workspace_id
    assert candidate.analysis_result_id == analysis_result.id
    assert candidate.status is LearningCandidateStatus.CANDIDATE_IDENTIFIED


def test_learning_candidate_status_enum_membership_is_exact() -> None:
    assert {s.value for s in LearningCandidateStatus} == {
        "CANDIDATE_IDENTIFIED", "PROVISIONAL", "VALIDATION_PENDING", "VALIDATED", "REJECTED", "INSUFFICIENT_EVIDENCE",
    }


def test_analysis_result_can_own_multiple_learning_candidates(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    service = LearningService(db_session)
    a = service.record_learning_candidate(analysis_result=analysis_result, summary="Insight A.")
    b = service.record_learning_candidate(analysis_result=analysis_result, summary="Insight B.")
    assert a.id != b.id
    assert a.analysis_result_id == b.analysis_result_id == analysis_result.id


def test_learning_candidate_workspace_mismatch_with_analysis_result_rejected_at_db_level(db_session) -> None:
    _campaign, analysis_result = build_analysis_result(db_session)
    other_workspace = WorkspaceRepository(db_session).create(
        organization_id=OrganizationRepository(db_session).create(name="Rogue Org").id, name="Rogue WS"
    )
    db_session.flush()

    rogue = LearningCandidate(
        public_id="LRN-MISMATCHTEST", workspace_id=other_workspace.id, analysis_result_id=analysis_result.id, summary="x",
    )
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_no_forbidden_fields_exist_on_learning_candidate() -> None:
    forbidden = (
        "updated_at", "campaign_id", "campaign_run_id", "stage_execution_id", "experiment_id", "content_piece_id",
        "strategy_id", "confidence", "replication_status", "consistency", "scope", "generalization_boundary",
        "evidence_basis", "reasoning", "payload", "version", "archived_at", "deleted_at",
    )
    columns = [c.lower() for c in LearningCandidate.__table__.columns.keys()]
    for term in forbidden:
        assert term not in columns, f"unexpected field {term!r} on LearningCandidate"


def test_learning_candidate_has_required_candidate_key() -> None:
    constraint_names = {c.name for c in LearningCandidate.__table__.constraints if getattr(c, "name", None)}
    assert "uq_learning_candidates_id_workspace_id" in constraint_names


# --- LearningCandidate: lifecycle -----------------------------------------


def test_every_allowed_transition_succeeds(db_session) -> None:
    _campaign, _analysis_result, candidate = build_learning_candidate(db_session)
    service = LearningService(db_session)
    candidate = service.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.PROVISIONAL)
    assert candidate.status is LearningCandidateStatus.PROVISIONAL
    candidate = service.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.VALIDATION_PENDING)
    assert candidate.status is LearningCandidateStatus.VALIDATION_PENDING
    candidate = service.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.INSUFFICIENT_EVIDENCE)
    assert candidate.status is LearningCandidateStatus.INSUFFICIENT_EVIDENCE
    candidate = service.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.VALIDATION_PENDING)
    assert candidate.status is LearningCandidateStatus.VALIDATION_PENDING
    candidate = service.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.VALIDATED)
    assert candidate.status is LearningCandidateStatus.VALIDATED


def test_validation_pending_can_reach_rejected(db_session) -> None:
    _campaign, _analysis_result, candidate = build_learning_candidate(db_session)
    service = LearningService(db_session)
    service.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.PROVISIONAL)
    service.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.VALIDATION_PENDING)
    candidate = service.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.REJECTED)
    assert candidate.status is LearningCandidateStatus.REJECTED


@pytest.mark.parametrize(
    "current,target",
    [
        (LearningCandidateStatus.CANDIDATE_IDENTIFIED, LearningCandidateStatus.VALIDATED),
        (LearningCandidateStatus.CANDIDATE_IDENTIFIED, LearningCandidateStatus.VALIDATION_PENDING),
        (LearningCandidateStatus.PROVISIONAL, LearningCandidateStatus.VALIDATED),
        (LearningCandidateStatus.PROVISIONAL, LearningCandidateStatus.REJECTED),
        (LearningCandidateStatus.VALIDATED, LearningCandidateStatus.VALIDATION_PENDING),
        (LearningCandidateStatus.REJECTED, LearningCandidateStatus.VALIDATION_PENDING),
        (LearningCandidateStatus.INSUFFICIENT_EVIDENCE, LearningCandidateStatus.VALIDATED),
        (LearningCandidateStatus.INSUFFICIENT_EVIDENCE, LearningCandidateStatus.REJECTED),
    ],
)
def test_every_forbidden_transition_is_rejected(current, target) -> None:
    assert not is_legal_learning_candidate_transition(current, target)


def test_validated_and_rejected_are_terminal() -> None:
    assert LEARNING_CANDIDATE_TRANSITIONS[LearningCandidateStatus.VALIDATED] == frozenset()
    assert LEARNING_CANDIDATE_TRANSITIONS[LearningCandidateStatus.REJECTED] == frozenset()


def test_insufficient_evidence_returns_only_to_validation_pending() -> None:
    assert LEARNING_CANDIDATE_TRANSITIONS[LearningCandidateStatus.INSUFFICIENT_EVIDENCE] == frozenset(
        {LearningCandidateStatus.VALIDATION_PENDING}
    )


def test_invalid_transition_raises_and_causes_no_mutation(db_session) -> None:
    _campaign, _analysis_result, candidate = build_learning_candidate(db_session)
    service = LearningService(db_session)
    with pytest.raises(InvalidLifecycleTransitionError):
        service.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.VALIDATED)
    db_session.refresh(candidate)
    assert candidate.status is LearningCandidateStatus.CANDIDATE_IDENTIFIED


# --- StrategicRecommendationCandidate: domain persistence -----------------


def test_recommendation_requires_validated_parent(db_session) -> None:
    _campaign, _analysis_result, candidate = build_learning_candidate(db_session)
    with pytest.raises(LearningCandidateNotValidatedError):
        LearningService(db_session).record_strategic_recommendation_candidate(learning_candidate=candidate, summary="x")


def test_recommendation_created_from_validated_parent(db_session) -> None:
    _campaign, _analysis_result, candidate, recommendation = build_recommendation(db_session)
    assert recommendation.public_id.startswith("SRC-")
    assert recommendation.learning_candidate_id == candidate.id
    assert recommendation.workspace_id == candidate.workspace_id
    assert recommendation.decision is None
    assert recommendation.decided_at is None


def test_recommendation_workspace_mismatch_with_learning_candidate_rejected_at_db_level(db_session) -> None:
    _campaign, _analysis_result, candidate = build_validated_learning_candidate(db_session)
    other_workspace = WorkspaceRepository(db_session).create(
        organization_id=OrganizationRepository(db_session).create(name="Rogue Org 2").id, name="Rogue WS 2"
    )
    db_session.flush()

    rogue = StrategicRecommendationCandidate(
        public_id="SRC-MISMATCHTEST", workspace_id=other_workspace.id, learning_candidate_id=candidate.id, summary="x",
    )
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_no_forbidden_fields_exist_on_src() -> None:
    forbidden = (
        "updated_at", "campaign_id", "campaign_run_id", "analysis_result_id", "strategy_id", "experiment_id",
        "content_piece_id", "version", "archived_at", "deleted_at", "status",
    )
    columns = [c.lower() for c in StrategicRecommendationCandidate.__table__.columns.keys()]
    for term in forbidden:
        assert term not in columns, f"unexpected field {term!r} on StrategicRecommendationCandidate"


def test_src_has_no_speculative_candidate_key() -> None:
    constraint_names = {c.name for c in StrategicRecommendationCandidate.__table__.constraints if getattr(c, "name", None)}
    assert "uq_strategic_recommendation_candidates_id_workspace_id" not in constraint_names


def test_recommendation_decision_enum_membership_is_exact() -> None:
    assert {d.value for d in StrategicRecommendationDecision} == {"ACCEPTED", "REJECTED"}


def test_learning_candidate_can_own_multiple_recommendations(db_session) -> None:
    _campaign, _analysis_result, candidate = build_validated_learning_candidate(db_session)
    service = LearningService(db_session)
    a = service.record_strategic_recommendation_candidate(learning_candidate=candidate, summary="Option A.")
    b = service.record_strategic_recommendation_candidate(learning_candidate=candidate, summary="Option B.")
    assert a.id != b.id
    assert a.learning_candidate_id == b.learning_candidate_id == candidate.id


def test_recording_recommendation_does_not_mutate_parent_candidate(db_session) -> None:
    _campaign, _analysis_result, candidate, _recommendation = build_recommendation(db_session)
    db_session.refresh(candidate)
    assert candidate.status is LearningCandidateStatus.VALIDATED


# --- StrategicRecommendationCandidate: decision semantics -----------------


def test_decision_starts_null(db_session) -> None:
    _campaign, _analysis_result, _candidate, recommendation = build_recommendation(db_session)
    assert recommendation.decision is None
    assert recommendation.decided_at is None


def test_governance_no_forbidden_tables_or_methods() -> None:
    from app.persistence.base import metadata

    for forbidden_table in ("strategic_decisions", "learning_candidate_versions", "learning_approvals"):
        assert forbidden_table not in metadata.tables.keys()

    forbidden_methods = ("create_campaign_version", "update", "delete", "patch", "validate_learning", "reject_learning_candidate")
    for method in forbidden_methods:
        assert not hasattr(LearningService, method), f"unexpected method {method!r} on LearningService"
