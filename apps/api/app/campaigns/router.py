"""Campaign API surface (BACKEND-05 §12). Every route depends on
``get_current_workspace`` (never a client-supplied ``workspace_id`` — see
``app/auth/dependencies.py``) and every mutation additionally depends on
``require_csrf``, exactly like the auth/workspaces routers.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_workspace, require_csrf
from app.campaigns.schemas import (
    CampaignCreateRequest,
    CampaignCreateResponse,
    CampaignListResponse,
    CampaignPatchRequest,
    CampaignPublic,
    CampaignRunListResponse,
    campaign_brief_to_public,
    campaign_run_to_public,
    campaign_to_public,
)
from app.campaigns.repository import CampaignRepository, CampaignRunRepository
from app.campaigns.service import CampaignAccessService, CampaignService
from app.persistence.session import get_db
from app.workspaces.models import Workspace

router = APIRouter(tags=["campaigns"])

_DEFAULT_PAGE_LIMIT = 20
_MAX_PAGE_LIMIT = 100


def _authorized_campaign(campaign_public_id: str, workspace: Workspace, db: Session):
    return CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )


@router.post("", response_model=CampaignCreateResponse, status_code=201, dependencies=[Depends(require_csrf)])
async def create_campaign(
    payload: CampaignCreateRequest,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CampaignCreateResponse:
    campaign, brief, run = CampaignService(db).create_campaign(
        workspace_id=workspace.id,
        name=payload.name,
        prompt=payload.prompt,
        product_type=payload.product_type,
        price=payload.price,
        audience=payload.audience,
        budget=payload.budget,
        channel=payload.channel,
    )
    return CampaignCreateResponse(
        campaign=campaign_to_public(campaign),
        brief=campaign_brief_to_public(brief, campaign_public_id=campaign.public_id),
        run=campaign_run_to_public(run, campaign_public_id=campaign.public_id),
    )


@router.get("", response_model=CampaignListResponse)
async def list_campaigns(
    limit: int = Query(default=_DEFAULT_PAGE_LIMIT, ge=1, le=_MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    include_archived: bool = Query(default=False),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CampaignListResponse:
    items, total = CampaignRepository(db).list_for_workspace(
        workspace_id=workspace.id, include_archived=include_archived, limit=limit, offset=offset
    )
    return CampaignListResponse(items=[campaign_to_public(item) for item in items], limit=limit, offset=offset, total=total)


@router.get("/{campaign_public_id}", response_model=CampaignPublic)
async def get_campaign(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CampaignPublic:
    campaign = _authorized_campaign(campaign_public_id, workspace, db)
    return campaign_to_public(campaign)


@router.patch("/{campaign_public_id}", response_model=CampaignPublic, dependencies=[Depends(require_csrf)])
async def patch_campaign(
    campaign_public_id: str,
    payload: CampaignPatchRequest,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CampaignPublic:
    campaign = _authorized_campaign(campaign_public_id, workspace, db)
    campaign = CampaignService(db).patch_campaign(campaign=campaign, name=payload.name)
    return campaign_to_public(campaign)


@router.post("/{campaign_public_id}/archive", response_model=CampaignPublic, dependencies=[Depends(require_csrf)])
async def archive_campaign(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CampaignPublic:
    campaign = _authorized_campaign(campaign_public_id, workspace, db)
    campaign = CampaignService(db).archive_campaign(campaign=campaign)
    return campaign_to_public(campaign)


@router.get("/{campaign_public_id}/runs", response_model=CampaignRunListResponse)
async def list_campaign_runs(
    campaign_public_id: str,
    limit: int = Query(default=_DEFAULT_PAGE_LIMIT, ge=1, le=_MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CampaignRunListResponse:
    campaign = _authorized_campaign(campaign_public_id, workspace, db)
    items, total = CampaignRunRepository(db).list_for_campaign(campaign_id=campaign.id, limit=limit, offset=offset)
    return CampaignRunListResponse(
        items=[campaign_run_to_public(item, campaign_public_id=campaign.public_id) for item in items],
        limit=limit,
        offset=offset,
        total=total,
    )
