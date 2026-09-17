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
from app.learning.models import LearningCandidate, LearningDerivation, StrategicImplication, StrategicRecommendationCandidate
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
            query = select(LearningCandidate).where(LearningCandidate.id == learning_candidate_id).with_for_update().execution_options(populate_existing=True)
            return self.session.execute(query).scalar_one_or_none()
        return self.session.get(LearningCandidate, learning_candidate_id)

    def get_for_campaign_by_public_id(
        self, *, campaign_id: uuid.UUID, public_id: str, for_update: bool = False
    ) -> LearningCandidate | None:
        """Non-leaky, campaign-scoped resource lookup (MVP-23B, mirrors
        ``StrategicRecommendationCandidateRepository.get_for_campaign_by_public_id``
        exactly, one join-hop shallower): a candidate that does not exist at
        all, that belongs to a different workspace, or that belongs to a
        different Campaign within the *same* workspace, is indistinguishable
        — all three return ``None`` here, and the service/router turn that
        into the same ``ForbiddenError`` either way. ``for_update=True`` is
        required before any call to ``LearningService.transition_learning_candidate``
        (MVP-23A §AJ / MVP-23B §8-§9) — state validity must be evaluated
        against the row loaded under this lock, never against a pre-lock
        read."""
        query = (
            select(LearningCandidate)
            .join(AnalysisResult, LearningCandidate.analysis_result_id == AnalysisResult.id)
            .where(AnalysisResult.campaign_id == campaign_id, LearningCandidate.public_id == public_id)
        )
        if for_update:
            query = query.with_for_update(of=LearningCandidate).execution_options(populate_existing=True)
        return self.session.execute(query).scalar_one_or_none()

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

    def create(
        self,
        *,
        learning_candidate: LearningCandidate,
        summary: str,
        strategic_implication: StrategicImplication,
    ) -> StrategicRecommendationCandidate:
        row = StrategicRecommendationCandidate(
            public_id=generate_public_id("SRC"),
            workspace_id=learning_candidate.workspace_id,
            learning_candidate_id=learning_candidate.id,
            strategic_implication_id=strategic_implication.id,
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


class StrategicImplicationRepository:
    """Data access for StrategicImplication (MVP-26) — mirrors
    ``StrategicRecommendationCandidateRepository`` exactly, one level
    shallower in the chain. No method here calls ``session.commit()``,
    same transaction-ownership convention as every repository in this
    module. No update/delete method exists — StrategicImplication is
    immutable from INSERT (MVP-26 §8)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, learning_candidate: LearningCandidate, statement: str) -> StrategicImplication:
        row = StrategicImplication(
            public_id=generate_public_id("SIM"),
            workspace_id=learning_candidate.workspace_id,
            learning_candidate_id=learning_candidate.id,
            statement=statement,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_for_campaign_by_public_id(
        self, *, campaign_id: uuid.UUID, public_id: str
    ) -> StrategicImplication | None:
        """Non-leaky, campaign-scoped resource lookup (MVP-26 §10, mirrors
        ``LearningCandidateRepository.get_for_campaign_by_public_id``
        exactly): an implication that does not exist at all, that belongs
        to a different workspace, or that belongs to a different Campaign
        within the *same* workspace, is indistinguishable — all three
        return ``None`` here. No ``for_update`` — StrategicImplication is
        immutable, so no caller ever needs to lock this row itself (the
        canonical serialization resource for every mutation that touches
        it remains ``LearningCandidate``, per MVP-26 §12)."""
        query = (
            select(StrategicImplication)
            .join(LearningCandidate, StrategicImplication.learning_candidate_id == LearningCandidate.id)
            .join(AnalysisResult, LearningCandidate.analysis_result_id == AnalysisResult.id)
            .where(AnalysisResult.campaign_id == campaign_id, StrategicImplication.public_id == public_id)
        )
        return self.session.execute(query).scalar_one_or_none()

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[StrategicImplication]:
        """Campaign-scoped, joined through LearningCandidate ->
        AnalysisResult — never workspace-wide, matching every other
        listing in this module. Used for the batched, N+1-avoiding
        campaign-wide read (MVP-26 §26)."""
        return list(
            self.session.execute(
                select(StrategicImplication)
                .join(LearningCandidate, StrategicImplication.learning_candidate_id == LearningCandidate.id)
                .join(AnalysisResult, LearningCandidate.analysis_result_id == AnalysisResult.id)
                .where(AnalysisResult.campaign_id == campaign_id)
                .order_by(StrategicImplication.created_at.asc(), StrategicImplication.id.asc())
            )
            .scalars()
            .all()
        )

    def list_for_candidate(self, learning_candidate_id: uuid.UUID) -> list[StrategicImplication]:
        """Single-candidate accessor for callers that already resolved and
        authorized exactly one LearningCandidate (e.g. after a mutation on
        it) — never used for a campaign-wide listing, where
        ``list_for_campaign`` above avoids N+1 instead."""
        return list(
            self.session.execute(
                select(StrategicImplication)
                .where(StrategicImplication.learning_candidate_id == learning_candidate_id)
                .order_by(StrategicImplication.created_at.asc(), StrategicImplication.id.asc())
            )
            .scalars()
            .all()
        )


class LearningDerivationRepository:
    """Data access for LearningDerivation — the Measurement -> Learning
    bridge's own provenance/identity row (MVP-12B-A/-R1). No method here
    calls ``session.commit()``, matching this module's own transaction-
    ownership convention exactly."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, analysis_result: AnalysisResult, learning_candidate: LearningCandidate) -> LearningDerivation:
        row = LearningDerivation(
            workspace_id=analysis_result.workspace_id,
            analysis_result_id=analysis_result.id,
            learning_candidate_id=learning_candidate.id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_analysis_result_id(self, analysis_result_id: uuid.UUID) -> LearningDerivation | None:
        return self.session.execute(
            select(LearningDerivation).where(LearningDerivation.analysis_result_id == analysis_result_id)
        ).scalar_one_or_none()

    def get_by_learning_candidate_id(self, learning_candidate_id: uuid.UUID) -> LearningDerivation | None:
        return self.session.execute(
            select(LearningDerivation).where(LearningDerivation.learning_candidate_id == learning_candidate_id)
        ).scalar_one_or_none()
