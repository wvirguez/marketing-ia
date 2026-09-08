"""Public DTOs for the planning read surface. Never expose an internal
UUID or a raw ``workspace_id`` — only public_id-derived fields (mirroring
``app/strategy/schemas.py``).

No field here ever represents approval, readiness, production authorization,
or a chain-of-thought/reasoning trace — see ``app/planning/models.py`` for
why none of those exist on the underlying models in the first place.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from app.planning.models import ContentPlan, PlanItem


class PlanItemPublic(BaseModel):
    id: str
    format: str
    objective: str
    sequence: int
    scheduled_date: date | None
    created_at: datetime


class ContentPlanPublic(BaseModel):
    id: str
    campaign_id: str
    version: int
    summary: str
    created_at: datetime


class PlanOutputResponse(BaseModel):
    plan: ContentPlanPublic | None
    items: list[PlanItemPublic]


def content_plan_to_public(plan: ContentPlan, *, campaign_public_id: str) -> ContentPlanPublic:
    return ContentPlanPublic(
        id=plan.public_id,
        campaign_id=campaign_public_id,
        version=plan.version,
        summary=plan.summary,
        created_at=plan.created_at,
    )


def plan_item_to_public(item: PlanItem) -> PlanItemPublic:
    return PlanItemPublic(
        id=item.public_id,
        format=item.format,
        objective=item.objective,
        sequence=item.sequence,
        scheduled_date=item.scheduled_date,
        created_at=item.created_at,
    )
