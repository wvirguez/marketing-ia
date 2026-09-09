"""Content API surface (BACKEND-10 §28) — the exact, canonical, GET-only
routes `docs/backend/BACKEND-01-API-MAP.md` §2 defines:

    GET /api/v1/campaigns/{campaign_id}/content
    GET /api/v1/campaigns/{campaign_id}/content/{content_id}

No Content Brief route and no Approval route exist here — both are
explicitly deferred (§29/§30 of the Governance Freeze). Writes are
service-layer only (``app/content/service.py``); nothing here ever calls a
mutating ``ContentService`` method.

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching the same bounded-context separation already applied to
``app/strategy/router.py``/``app/planning/router.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_workspace
from app.campaigns.service import CampaignAccessService
from app.content.schemas import (
    ContentPieceDetailResponse,
    ContentPieceListResponse,
    content_piece_to_public,
    content_version_to_public,
)
from app.content.service import ContentService
from app.core.api_errors import ForbiddenError
from app.persistence.session import get_db
from app.workspaces.models import Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}", tags=["content"])


@router.get("/content", response_model=ContentPieceListResponse)
async def list_content(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceListResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    pieces = ContentService(db).list_pieces_for_campaign(campaign_id=campaign.id)
    return ContentPieceListResponse(items=[content_piece_to_public(p) for p in pieces])


@router.get("/content/{content_public_id}", response_model=ContentPieceDetailResponse)
async def get_content_detail(
    campaign_public_id: str,
    content_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    result = ContentService(db).get_piece_detail_for_campaign(
        campaign_id=campaign.id, content_piece_public_id=content_public_id
    )
    if result is None:
        # Non-leaky: a Content Piece that does not exist, or that exists
        # but belongs to a different campaign, is indistinguishable
        # (mirrors CampaignAccessService's own precedent).
        raise ForbiddenError()
    piece, latest_version = result
    return ContentPieceDetailResponse(
        piece=content_piece_to_public(piece),
        latest_version=content_version_to_public(latest_version) if latest_version else None,
    )
