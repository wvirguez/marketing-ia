"""Tracking API surface (BACKEND-15 Governance Freeze §M/§N, repaired by
Freeze-R for Requirement mutation lifecycle) — exactly:

    GET   /api/v1/campaigns/{campaign_id}/tracking
    PATCH /api/v1/campaigns/{campaign_id}/tracking

No POST exists for TrackingPlan or TrackingRequirement creation — both
remain service-layer-only (``app/tracking/service.py``). PATCH is a
strict, discriminated single-operation body: either ``TRANSITION_PLAN``
or ``UPDATE_REQUIREMENT_STATUS`` — never both, never arbitrary field
patching.

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching the same bounded-context separation already applied to
``app/learning/router.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf
from app.campaigns.service import CampaignAccessService
from app.core.api_errors import ForbiddenError
from app.tracking.schemas import (
    TrackingPatchRequest,
    TrackingResponse,
    UpdateRequirementStatusOperation,
    tracking_plan_to_public,
)
from app.tracking.service import TrackingService
from app.persistence.session import get_db
from app.users.models import User
from app.workspaces.models import Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}/tracking", tags=["tracking"])


@router.get("", response_model=TrackingResponse)
async def get_tracking(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> TrackingResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = TrackingService(db)
    plan = service.get_plan_for_campaign(campaign_id=campaign.id)
    if plan is None:
        return TrackingResponse(plan=None)

    requirements = service.list_requirements_for_plan(tracking_plan_id=plan.id)
    return TrackingResponse(plan=tracking_plan_to_public(plan, requirements=requirements))


@router.patch("", response_model=TrackingResponse, dependencies=[Depends(require_csrf)])
async def patch_tracking(
    campaign_public_id: str,
    payload: TrackingPatchRequest,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> TrackingResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = TrackingService(db)

    if isinstance(payload, UpdateRequirementStatusOperation):
        service.update_tracking_requirement_status(
            campaign=campaign,
            requirement_public_id=payload.requirement_id,
            status=payload.status,
            actor_user_id=user.id,
        )
    else:
        service.transition_tracking_plan(
            campaign=campaign, target_status=payload.target_status, actor_user_id=user.id,
        )

    plan = service.get_plan_for_campaign(campaign_id=campaign.id)
    if plan is None:
        # Cannot happen — both operations above require an existing Plan
        # (transition_tracking_plan/update_tracking_requirement_status
        # both raise ForbiddenError when none exists) — defensive only.
        raise ForbiddenError()
    requirements = service.list_requirements_for_plan(tracking_plan_id=plan.id)
    return TrackingResponse(plan=tracking_plan_to_public(plan, requirements=requirements))
