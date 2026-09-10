"""Public DTOs for the Learning read/decide surface. Never expose an
internal UUID, a raw ``workspace_id``, or a raw campaign UUID — only
public_id-derived fields and cross-references (mirroring
``app/measurement/schemas.py``'s own ``source_*_ids`` cross-reference
pattern).

No field here ever represents confidence, replication status, consistency,
scope, generalization boundary, evidence basis, or a chain-of-thought/
reasoning trace (Governance Freeze §K/§I).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.learning.models import LearningCandidate, LearningCandidateStatus, StrategicRecommendationCandidate, StrategicRecommendationDecision


class LearningCandidatePublic(BaseModel):
    id: str
    analysis_result_id: str
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


def learning_candidate_to_public(candidate: LearningCandidate, *, analysis_result_public_id: str) -> LearningCandidatePublic:
    return LearningCandidatePublic(
        id=candidate.public_id,
        analysis_result_id=analysis_result_public_id,
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
