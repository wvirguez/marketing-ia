"""Data access for Content Plan / Plan Item. No repository here calls
``session.commit()`` — see ``app/persistence/session.py`` and
``app/planning/service.py`` for the transaction-ownership boundary.

Every ``create``/``create_many`` method takes the parent domain object
(``Campaign``, ``CampaignRun``, ``RunStageExecution``, ``ContentPlan``),
never a raw ``workspace_id``/``campaign_id``/``content_plan_id`` parameter —
the same "no independent parameter, no possibility of drift" pattern
established in ``app/strategy/repository.py``/``app/research/repository.py``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign, CampaignRun
from app.core.ids import generate_public_id
from app.orchestration.models import RunStageExecution
from app.planning.models import ContentPlan, PlanItem


class ContentPlanRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        campaign: Campaign,
        campaign_run: CampaignRun,
        stage_execution: RunStageExecution,
        version: int,
        summary: str,
    ) -> ContentPlan:
        plan = ContentPlan(
            public_id=generate_public_id("PLN"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            campaign_run_id=campaign_run.id,
            stage_execution_id=stage_execution.id,
            version=version,
            summary=summary,
        )
        self.session.add(plan)
        self.session.flush()
        return plan

    def get_by_public_id(self, public_id: str) -> ContentPlan | None:
        return self.session.execute(select(ContentPlan).where(ContentPlan.public_id == public_id)).scalar_one_or_none()

    def get_current_for_campaign(self, campaign_id: uuid.UUID) -> ContentPlan | None:
        return self.session.execute(
            select(ContentPlan).where(ContentPlan.campaign_id == campaign_id).order_by(ContentPlan.version.desc()).limit(1)
        ).scalar_one_or_none()

    def next_version_for_campaign(self, campaign_id: uuid.UUID) -> int:
        current = self.session.execute(
            select(func.max(ContentPlan.version)).where(ContentPlan.campaign_id == campaign_id)
        ).scalar_one()
        return (current or 0) + 1


class PlanItemRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_many(self, *, plan: ContentPlan, items: list[dict]) -> list[PlanItem]:
        rows = [
            PlanItem(
                public_id=generate_public_id("ITM"),
                content_plan_id=plan.id,
                format=item["format"],
                objective=item["objective"],
                sequence=item["sequence"],
                scheduled_date=item.get("scheduled_date"),
            )
            for item in items
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def list_for_plan(self, content_plan_id: uuid.UUID) -> list[PlanItem]:
        return list(
            self.session.execute(
                select(PlanItem).where(PlanItem.content_plan_id == content_plan_id).order_by(PlanItem.sequence.asc(), PlanItem.id.asc())
            )
            .scalars()
            .all()
        )
