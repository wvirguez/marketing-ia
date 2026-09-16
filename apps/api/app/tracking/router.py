"""Tracking API surface (BACKEND-15 Governance Freeze §M/§N, repaired by
Freeze-R for Requirement mutation lifecycle; creation added by MVP-15B) —
exactly:

    GET   /api/v1/campaigns/{campaign_id}/tracking
    POST  /api/v1/campaigns/{campaign_id}/tracking
    POST  /api/v1/campaigns/{campaign_id}/tracking/requirements
    PATCH /api/v1/campaigns/{campaign_id}/tracking

PATCH is a strict, discriminated single-operation body: either
``TRANSITION_PLAN`` or ``UPDATE_REQUIREMENT_STATUS`` — never both, never
arbitrary field patching. Creation is deliberately separate from PATCH
(MVP-15A Option B) — POST creates a resource, PATCH only ever transitions
or mutates an already-existing one; the two are never conflated.

TrackingPlan creation takes no request body — every field is either
fixed (status is always created at ``NOT_DEFINED``) or derived from the
already-authorized Campaign, so there is nothing for a caller to supply.

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching the same bounded-context separation already applied to
``app/learning/router.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf
from app.campaigns.service import CampaignAccessService
from app.content.repository import ContentDistributionRepository, ContentDistributionTrackingRequirementRepository
from app.core.api_errors import ForbiddenError
from app.tracking.models import TrackingRequirement
from app.tracking.schemas import (
    CreateTrackingRequirementRequest,
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


def _associated_distribution_ids_by_requirement_id(
    db: Session, requirements: list[TrackingRequirement]
) -> dict:
    """MVP-24: identity-only (MVP-24A-R1) cross-domain read — resolves
    each Requirement's associated ContentDistribution *public* ids via
    the association table, never reading/copying any ContentDistribution
    field beyond its id. Cross-domain resolution happens here, in the
    router, matching this codebase's own established convention (e.g.
    ``app/learning/router.py`` resolving AnalysisResult public ids)."""
    association_repo = ContentDistributionTrackingRequirementRepository(db)
    distribution_repo = ContentDistributionRepository(db)
    result: dict = {}
    for requirement in requirements:
        distribution_ids = association_repo.list_distribution_ids_for_requirement(requirement.id)
        distributions = distribution_repo.list_for_ids(distribution_ids)
        result[requirement.id] = [d.public_id for d in distributions]
    return result


def _current_tracking_response(service: TrackingService, *, campaign_id) -> TrackingResponse:
    plan = service.get_plan_for_campaign(campaign_id=campaign_id)
    if plan is None:
        # Cannot happen for either creation route below — both either
        # just created the Plan or require one to already exist —
        # defensive only, mirrors patch_tracking's own guard.
        raise ForbiddenError()
    requirements = service.list_requirements_for_plan(tracking_plan_id=plan.id)
    by_id = _associated_distribution_ids_by_requirement_id(service.session, requirements)
    return TrackingResponse(
        plan=tracking_plan_to_public(plan, requirements=requirements, associated_distribution_ids_by_requirement_id=by_id)
    )


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
    by_id = _associated_distribution_ids_by_requirement_id(db, requirements)
    return TrackingResponse(
        plan=tracking_plan_to_public(plan, requirements=requirements, associated_distribution_ids_by_requirement_id=by_id)
    )


@router.post("", response_model=TrackingResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_csrf)])
async def create_tracking_plan(
    campaign_public_id: str,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> TrackingResponse:
    """No request body — see the module docstring. Duplicate creation
    (sequential or genuinely concurrent) always resolves to
    ``TrackingPlanAlreadyExistsError`` -> 409 (``app/tracking/service.py``'s
    MVP-15B concurrency repair), never a raw 500."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = TrackingService(db)
    service.record_tracking_plan(campaign=campaign, actor_user_id=user.id)
    return _current_tracking_response(service, campaign_id=campaign.id)


@router.post(
    "/requirements", response_model=TrackingResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_csrf)]
)
async def create_tracking_requirement(
    campaign_public_id: str,
    payload: CreateTrackingRequirementRequest,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> TrackingResponse:
    """The Plan is resolved through the already-authorized Campaign —
    never from a client-supplied identifier. No Plan -> ``ForbiddenError``
    (403), matching the existing PATCH-with-no-plan convention exactly
    (never a distinct error class for the same underlying condition)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = TrackingService(db)
    plan = service.get_plan_for_campaign(campaign_id=campaign.id)
    if plan is None:
        raise ForbiddenError()
    service.record_tracking_requirement(tracking_plan=plan, name=payload.name, actor_user_id=user.id)
    return _current_tracking_response(service, campaign_id=campaign.id)


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

    return _current_tracking_response(service, campaign_id=campaign.id)
