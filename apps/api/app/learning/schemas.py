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

from app.learning.models import LearningCandidate, LearningCandidateStatus, StrategicRecommendationCandidate, StrategicRecommendationDecision


from app.learning.qualification_schemas import QualificationPublic


class LearningCandidatePublic(BaseModel):
    id: str
    analysis_result_id: str
    qualification: QualificationPublic | None = None
    status: LearningCandidateStatus
    summary: str
    created_at: datetime


class StrategicRecommendationCandidatePublic(BaseModel):
    id: str
    learning_candidate_id: str
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


class CreateRecommendationRequest(BaseModel):
    """MVP-23B: the sole writable field for a new
    ``StrategicRecommendationCandidate`` — no client-supplied ``status``,
    ``campaign_id``, ``learning_candidate_id``, or ``actor``; all of those
    are derived from the URL/session."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=4000)


def learning_candidate_to_public(candidate: LearningCandidate, *, analysis_result_public_id: str, qualification: QualificationPublic | None = None) -> LearningCandidatePublic:
    return LearningCandidatePublic(
        id=candidate.public_id,
        analysis_result_id=analysis_result_public_id,
        qualification=qualification,
        status=candidate.status,
        summary=candidate.summary,
        created_at=candidate.created_at,
    )


def recommendation_to_public(
    recommendation: StrategicRecommendationCandidate, *, learning_candidate_public_id: str
) -> StrategicRecommendationCandidatePublic:
    return StrategicRecommendationCandidatePublic(
        id=recommendation.public_id,
        learning_candidate_id=learning_candidate_public_id,
        summary=recommendation.summary,
        decision=recommendation.decision,
        created_at=recommendation.created_at,
        decided_at=recommendation.decided_at,
    )
