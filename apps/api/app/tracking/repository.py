"""Data access for TrackingPlan / TrackingRequirement.
No repository here calls ``session.commit()`` — see
``app/persistence/session.py`` and ``app/tracking/service.py`` for the
transaction-ownership boundary.

Every ``create`` method takes the parent domain object (``Campaign``,
``TrackingPlan``), never a raw ``workspace_id``/``campaign_id``/
``tracking_plan_id`` parameter — the same "no independent parameter, no
possibility of drift" pattern established throughout this codebase.

Campaign-scoped lookups (BACKEND-15 Governance Freeze, TRK-D15) constrain
directly on ``TrackingPlan.campaign_id`` (a direct column, unlike Learning's
multi-hop AnalysisResult traversal) or, for Requirement mutation, JOIN
through ``TrackingRequirement -> TrackingPlan.campaign_id`` — never filter
by ``workspace_id`` alone.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign
from app.core.ids import generate_public_id
from app.tracking.models import TrackingPlan, TrackingRequirement


class TrackingPlanRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, campaign: Campaign) -> TrackingPlan:
        row = TrackingPlan(
            public_id=generate_public_id("TRK"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_for_campaign(self, *, campaign_id: uuid.UUID, for_update: bool = False) -> TrackingPlan | None:
        """Direct, one-hop, campaign-scoped lookup — TrackingPlan carries
        campaign_id itself, so no JOIN chain is needed (BACKEND-15
        Governance Freeze §P/TRK-D15)."""
        query = select(TrackingPlan).where(TrackingPlan.campaign_id == campaign_id)
        if for_update:
            query = query.with_for_update()
        return self.session.execute(query).scalar_one_or_none()


class TrackingRequirementRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, tracking_plan: TrackingPlan, name: str) -> TrackingRequirement:
        row = TrackingRequirement(
            public_id=generate_public_id("TRQ"),
            tracking_plan_id=tracking_plan.id,
            name=name,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def count_for_plan(self, *, tracking_plan_id: uuid.UUID) -> int:
        return self.session.execute(
            select(func.count()).select_from(TrackingRequirement).where(TrackingRequirement.tracking_plan_id == tracking_plan_id)
        ).scalar_one()

    def get_for_campaign_by_public_id(
        self, *, campaign_id: uuid.UUID, public_id: str, for_update: bool = False
    ) -> TrackingRequirement | None:
        """Non-leaky, campaign-scoped resource lookup (BACKEND-15
        Governance Freeze, TRK-D15): a requirement that does not exist at
        all, that belongs to a different workspace, or that belongs to a
        different Campaign within the *same* workspace, is
        indistinguishable — all three return ``None`` here, and the
        service/router turn that into the same ``ForbiddenError`` either
        way (mirrors ``StrategicRecommendationCandidateRepository.
        get_for_campaign_by_public_id`` exactly)."""
        query = (
            select(TrackingRequirement)
            .join(TrackingPlan, TrackingRequirement.tracking_plan_id == TrackingPlan.id)
            .where(TrackingPlan.campaign_id == campaign_id, TrackingRequirement.public_id == public_id)
        )
        if for_update:
            query = query.with_for_update()
        return self.session.execute(query).scalar_one_or_none()

    def list_for_plan(self, *, tracking_plan_id: uuid.UUID) -> list[TrackingRequirement]:
        """Deterministic ordering (BACKEND-15 Governance Freeze §AD/
        TRK-D34) — created_at ASC, id ASC, mirroring
        LearningCandidateRepository.list_for_campaign's exact tie-breaker
        pattern. The id tie-breaker is deterministic only, never
        chronological, and is never exposed publicly."""
        return list(
            self.session.execute(
                select(TrackingRequirement)
                .where(TrackingRequirement.tracking_plan_id == tracking_plan_id)
                .order_by(TrackingRequirement.created_at.asc(), TrackingRequirement.id.asc())
            )
            .scalars()
            .all()
        )
