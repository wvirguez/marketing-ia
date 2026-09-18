"""Planning API surface (BACKEND-09 §18), extended by MVP-33B with one
governed write route:

    GET  /api/v1/campaigns/{campaign_id}/plan
    POST /api/v1/campaigns/{campaign_id}/plan  (MVP-33A/-33A-R1/MVP-33B)

The GET route is the exact, canonical route `docs/backend/
BACKEND-01-API-MAP.md` §2 originally defined as GET-only; deterministic
bootstrap output (``record_plan``, ``origin=BOOTSTRAP``) remains writable
only through ``app/orchestration/service.py``'s own bootstrap flow, never
through this router. The new POST route calls
``PlanningService.create_plan`` (``origin=GOVERNED``) exclusively.

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching the same bounded-context separation already applied to
``app/strategy/router.py``/``app/research/router.py``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf
from app.campaigns.service import CampaignAccessService
from app.persistence.session import get_db
from app.planning.schemas import (
    CreateContentPlanRequest,
    PlanOutputResponse,
    content_plan_to_public,
    plan_item_to_public,
)
from app.planning.service import PlanningService
from app.strategy.repository import ExperimentRepository
from app.users.models import User
from app.workspaces.models import Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}", tags=["planning"])


def _experiment_public_id(db: Session, experiment_id: uuid.UUID | None) -> str | None:
    if experiment_id is None:
        return None
    experiment = ExperimentRepository(db).get_by_id(experiment_id)
    return experiment.public_id if experiment is not None else None


@router.get("/plan", response_model=PlanOutputResponse)
async def get_plan(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> PlanOutputResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    plan, items = PlanningService(db).get_plan_output(campaign=campaign)
    return PlanOutputResponse(
        plan=(
            content_plan_to_public(
                plan, campaign_public_id=campaign.public_id,
                experiment_public_id=_experiment_public_id(db, plan.experiment_id),
            )
            if plan
            else None
        ),
        items=[plan_item_to_public(item) for item in items],
    )


@router.post(
    "/plan",
    response_model=PlanOutputResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_plan(
    campaign_public_id: str,
    payload: CreateContentPlanRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> PlanOutputResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    plan, items = PlanningService(db).create_plan(
        campaign=campaign,
        summary=payload.summary,
        experiment_public_id=payload.experiment_public_id,
        items=[item.model_dump() for item in payload.items] if payload.items else None,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return PlanOutputResponse(
        plan=content_plan_to_public(
            plan, campaign_public_id=campaign.public_id,
            experiment_public_id=_experiment_public_id(db, plan.experiment_id),
        ),
        items=[plan_item_to_public(item) for item in items],
    )
