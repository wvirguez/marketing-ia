"""Data access for LearningCandidate / StrategicRecommendationCandidate.
No repository here calls ``session.commit()`` — see
``app/persistence/session.py`` and ``app/learning/service.py`` for the
transaction-ownership boundary.

Every ``create`` method takes the parent domain object (``AnalysisResult``,
``LearningCandidate``), never a raw ``workspace_id``/``analysis_result_id``/
``learning_candidate_id`` parameter — the same "no independent parameter,
no possibility of drift" pattern established throughout this codebase.

Campaign-scoped lookups/listings (BACKEND-14 Governance Freeze-R, GF-D26)
join through to ``AnalysisResult.campaign_id`` — never filter by
``workspace_id`` alone. Neither table carries its own ``campaign_id``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import generate_public_id
from app.learning.models import LearningCandidate, StrategicRecommendationCandidate
from app.measurement.models import AnalysisResult


class LearningCandidateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, analysis_result: AnalysisResult, summary: str) -> LearningCandidate:
        row = LearningCandidate(
            public_id=generate_public_id("LRN"),
            workspace_id=analysis_result.workspace_id,
            analysis_result_id=analysis_result.id,
            summary=summary,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_id(self, learning_candidate_id: uuid.UUID, *, for_update: bool = False) -> LearningCandidate | None:
        if for_update:
            query = select(LearningCandidate).where(LearningCandidate.id == learning_candidate_id).with_for_update()
            return self.session.execute(query).scalar_one_or_none()
        return self.session.get(LearningCandidate, learning_candidate_id)

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[LearningCandidate]:
        """Campaign-scoped, joined through AnalysisResult — never
        workspace-wide (Governance Freeze-R GF-D26)."""
        return list(
            self.session.execute(
                select(LearningCandidate)
                .join(AnalysisResult, LearningCandidate.analysis_result_id == AnalysisResult.id)
                .where(AnalysisResult.campaign_id == campaign_id)
                .order_by(LearningCandidate.created_at.asc(), LearningCandidate.id.asc())
            )
            .scalars()
            .all()
        )


class StrategicRecommendationCandidateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, learning_candidate: LearningCandidate, summary: str) -> StrategicRecommendationCandidate:
        row = StrategicRecommendationCandidate(
            public_id=generate_public_id("SRC"),
            workspace_id=learning_candidate.workspace_id,
            learning_candidate_id=learning_candidate.id,
            summary=summary,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_for_campaign_by_public_id(
        self, *, campaign_id: uuid.UUID, public_id: str, for_update: bool = False
    ) -> StrategicRecommendationCandidate | None:
        """Non-leaky, campaign-scoped resource lookup (BACKEND-14
        Governance Freeze-R, GF-D26): a recommendation that does not exist
        at all, that belongs to a different workspace, or that belongs to
        a different Campaign within the *same* workspace, is
        indistinguishable — all three return ``None`` here, and the
        service/router turn that into the same ``ForbiddenError`` either
        way (mirrors ``ContentPieceRepository.get_for_campaign_by_public_id``
        exactly, one join-hop deeper)."""
        query = (
            select(StrategicRecommendationCandidate)
            .join(
                LearningCandidate,
                StrategicRecommendationCandidate.learning_candidate_id == LearningCandidate.id,
            )
            .join(AnalysisResult, LearningCandidate.analysis_result_id == AnalysisResult.id)
            .where(
                AnalysisResult.campaign_id == campaign_id,
                StrategicRecommendationCandidate.public_id == public_id,
            )
        )
        if for_update:
            query = query.with_for_update()
        return self.session.execute(query).scalar_one_or_none()

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[StrategicRecommendationCandidate]:
        """Campaign-scoped, joined through LearningCandidate ->
        AnalysisResult — never workspace-wide (Governance Freeze-R
        GF-D26)."""
        return list(
            self.session.execute(
                select(StrategicRecommendationCandidate)
                .join(
                    LearningCandidate,
                    StrategicRecommendationCandidate.learning_candidate_id == LearningCandidate.id,
                )
                .join(AnalysisResult, LearningCandidate.analysis_result_id == AnalysisResult.id)
                .where(AnalysisResult.campaign_id == campaign_id)
                .order_by(StrategicRecommendationCandidate.created_at.asc(), StrategicRecommendationCandidate.id.asc())
            )
            .scalars()
            .all()
        )
