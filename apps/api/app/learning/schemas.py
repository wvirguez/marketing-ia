"""Public DTOs for the Learning read/decide surface. Never expose an
internal UUID, a raw ``workspace_id``, or a raw campaign UUID — only
public_id-derived fields and cross-references (mirroring
``app/measurement/schemas.py``'s own ``source_*_ids`` cross-reference
pattern).

MVP-25 adds explicit human qualification. Consistency is derived; VALIDATED
is bounded acceptance, never causal proof or system-verified replication.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.learning.models import LearningCandidate, LearningCandidateStatus, StrategicImplication, StrategicRecommendationCandidate, StrategicRecommendationDecision


from app.learning.qualification_schemas import QualificationPublic


class StrategicImplicationPublic(BaseModel):
    """MVP-26: a bounded, human-authored interpretation of a VALIDATED,
    sufficiently-qualified Learning. Not a Strategic Decision, not a
    Strategic Approval, not a Strategy mutation, not a causal or
    commercial claim — see the frontend governance copy this projection
    is paired with."""

    id: str
    learning_candidate_id: str
    statement: str
    created_at: datetime


class LearningCandidatePublic(BaseModel):
    id: str
    analysis_result_id: str
    qualification: QualificationPublic | None = None
    strategic_implications: list[StrategicImplicationPublic] = Field(default_factory=list)
    status: LearningCandidateStatus
    summary: str
    created_at: datetime


class StrategicRecommendationCandidatePublic(BaseModel):
    id: str
    learning_candidate_id: str
    # MVP-26/26A-R1: nullable in the read model for legacy rows created
    # before this linkage was required — this does NOT imply a new write
    # may omit it (enforced separately, see CreateRecommendationRequest).
    strategic_implication_id: str | None
    summary: str
    decision: StrategicRecommendationDecision | None
    created_at: datetime
    decided_at: datetime | None


class LearningResponse(BaseModel):
    learning_candidates: list[LearningCandidatePublic]
    strategic_recommendation_candidates: list[StrategicRecommendationCandidatePublic]


class RecommendationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: StrategicRecommendationDecision


class LearningCandidateDecisionRequest(BaseModel):
    """MVP-23B: the final governed Learning decision, restricted to
    exactly the three legal targets of ``VALIDATION_PENDING`` — never an
    arbitrary status string (mirrors ``RecordApprovalDecisionRequest``'s
    own ``Literal`` restriction exactly)."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal[
        LearningCandidateStatus.VALIDATED,
        LearningCandidateStatus.REJECTED,
        LearningCandidateStatus.INSUFFICIENT_EVIDENCE,
    ]


class CreateStrategicImplicationRequest(BaseModel):
    """MVP-26: the sole writable field for a new ``StrategicImplication``
    — no client-supplied ``status``, ``campaign_id``,
    ``learning_candidate_id``, or ``actor``; all of those are derived from
    the URL/session, mirroring ``CreateRecommendationRequest`` exactly."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=4000)


class CreateRecommendationRequest(BaseModel):
    """MVP-23B, extended by MVP-26/26A-R1: ``strategic_implication_id`` is
    REQUIRED (no default) — every NEW ``StrategicRecommendationCandidate``
    must reference exactly one ``StrategicImplication`` belonging to the
    same LearningCandidate (MVP-26A-R1 §E). This is an API-layer
    requirement only; the persisted column remains nullable so legacy
    rows created before this requirement existed stay valid (MVP-26A-R1
    §D) — database nullability and new-write optionality are deliberately
    different things here. No client-supplied ``status``, ``campaign_id``,
    ``learning_candidate_id``, or ``actor``; all of those are derived from
    the URL/session."""

    model_config = ConfigDict(extra="forbid")

    strategic_implication_id: str = Field(min_length=1, max_length=20)
    summary: str = Field(min_length=1, max_length=4000)


def learning_candidate_to_public(
    candidate: LearningCandidate,
    *,
    analysis_result_public_id: str,
    qualification: QualificationPublic | None = None,
    strategic_implications: list[StrategicImplicationPublic] | None = None,
) -> LearningCandidatePublic:
    return LearningCandidatePublic(
        id=candidate.public_id,
        analysis_result_id=analysis_result_public_id,
        qualification=qualification,
        strategic_implications=strategic_implications or [],
        status=candidate.status,
        summary=candidate.summary,
        created_at=candidate.created_at,
    )


def strategic_implication_to_public(
    implication: StrategicImplication, *, learning_candidate_public_id: str
) -> StrategicImplicationPublic:
    return StrategicImplicationPublic(
        id=implication.public_id,
        learning_candidate_id=learning_candidate_public_id,
        statement=implication.statement,
        created_at=implication.created_at,
    )


def recommendation_to_public(
    recommendation: StrategicRecommendationCandidate,
    *,
    learning_candidate_public_id: str,
    strategic_implication_public_id: str | None = None,
) -> StrategicRecommendationCandidatePublic:
    return StrategicRecommendationCandidatePublic(
        id=recommendation.public_id,
        learning_candidate_id=learning_candidate_public_id,
        strategic_implication_id=strategic_implication_public_id,
        summary=recommendation.summary,
        decision=recommendation.decision,
        created_at=recommendation.created_at,
        decided_at=recommendation.decided_at,
    )
