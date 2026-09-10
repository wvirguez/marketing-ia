"""Assets API surface (BACKEND-13 §15) — the exact, canonical, GET-only
route the Governance Freeze authorizes:

    GET /api/v1/campaigns/{campaign_id}/content/{content_id}/assets

No detail route exists (not frozen). Writes are service-layer only
(``app/assets/service.py``); nothing here ever calls a mutating
``AssetsService`` method.

Mounted directly on ``api_v1_router`` (not nested inside the campaigns or
content routers), matching the same bounded-context separation already
applied to ``app/content/router.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.assets.schemas import AssetsForContentPieceResponse, asset_to_public, creative_brief_to_public
from app.assets.service import AssetsService
from app.auth.dependencies import get_current_workspace
from app.campaigns.service import CampaignAccessService
from app.content.service import ContentService
from app.core.api_errors import ForbiddenError
from app.persistence.session import get_db
from app.workspaces.models import Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}/content/{content_public_id}", tags=["assets"])


@router.get("/assets", response_model=AssetsForContentPieceResponse)
async def list_assets(
    campaign_public_id: str,
    content_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> AssetsForContentPieceResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    piece_result = ContentService(db).get_piece_detail_for_campaign(
        campaign_id=campaign.id, content_piece_public_id=content_public_id
    )
    if piece_result is None:
        # Non-leaky: a Content Piece that does not exist, or that exists
        # but belongs to a different campaign, is indistinguishable
        # (mirrors app/content/router.py's own precedent).
        raise ForbiddenError()
    piece, _latest_version = piece_result

    assets_service = AssetsService(db)
    creative_brief = assets_service.creative_briefs.get_for_content_piece(piece.id)
    assets = assets_service.list_active_assets_for_content_piece(content_piece_id=piece.id)
    items = [
        asset_to_public(asset, current_version=assets_service.get_current_version_for_asset(asset.id))
        for asset in assets
    ]
    return AssetsForContentPieceResponse(
        creative_brief=creative_brief_to_public(creative_brief) if creative_brief is not None else None,
        assets=items,
    )
