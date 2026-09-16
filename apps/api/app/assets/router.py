"""Assets API surface (BACKEND-13 §15, creation added by MVP-16B) —
exactly:

    GET  /api/v1/campaigns/{campaign_id}/content/{content_id}/assets
    POST /api/v1/campaigns/{campaign_id}/content/{content_id}/assets/creative-brief
    POST /api/v1/campaigns/{campaign_id}/content/{content_id}/assets
    POST /api/v1/campaigns/{campaign_id}/content/{content_id}/assets/{asset_id}/versions

No detail route exists (not frozen). No archive route exists (MVP-16A
§V — deferred). Writes go through the existing, unmodified
``AssetsService`` methods only — no service/repository change was needed
for this slice (MVP-16A §BJ).

Mounted directly on ``api_v1_router`` (not nested inside the campaigns or
content routers), matching the same bounded-context separation already
applied to ``app/content/router.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.assets.schemas import (
    AssetsForContentPieceResponse,
    CreateAssetRequest,
    CreateAssetVersionRequest,
    CreateCreativeBriefRequest,
    asset_to_public,
    creative_brief_to_public,
)
from app.assets.service import AssetsService
from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf
from app.campaigns.service import CampaignAccessService
from app.content.service import ContentService
from app.core.api_errors import ForbiddenError
from app.persistence.session import get_db
from app.users.models import User
from app.workspaces.models import Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}/content/{content_public_id}", tags=["assets"])


def _current_assets_response(assets_service: AssetsService, *, content_piece_id) -> AssetsForContentPieceResponse:
    creative_brief = assets_service.creative_briefs.get_for_content_piece(content_piece_id)
    assets = assets_service.list_active_assets_for_content_piece(content_piece_id=content_piece_id)
    items = [
        asset_to_public(asset, current_version=assets_service.get_current_version_for_asset(asset.id))
        for asset in assets
    ]
    return AssetsForContentPieceResponse(
        creative_brief=creative_brief_to_public(creative_brief) if creative_brief is not None else None,
        assets=items,
    )


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


@router.post(
    "/assets/creative-brief",
    response_model=AssetsForContentPieceResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_creative_brief(
    campaign_public_id: str,
    content_public_id: str,
    payload: CreateCreativeBriefRequest,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> AssetsForContentPieceResponse:
    """``spec`` is passed through verbatim to the already-existing,
    already-race-safe ``record_creative_brief`` — a second Creative Brief
    for the same Content Piece (sequential or genuinely concurrent) always
    resolves to ``CreativeBriefAlreadyExistsError`` -> 409."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    piece_result = ContentService(db).get_piece_detail_for_campaign(
        campaign_id=campaign.id, content_piece_public_id=content_public_id
    )
    if piece_result is None:
        raise ForbiddenError()
    piece, _latest_version = piece_result

    assets_service = AssetsService(db)
    assets_service.record_creative_brief(content_piece=piece, spec=payload.spec, actor_user_id=user.id)
    return _current_assets_response(assets_service, content_piece_id=piece.id)


@router.post(
    "/assets", response_model=AssetsForContentPieceResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_csrf)]
)
async def create_asset(
    campaign_public_id: str,
    content_public_id: str,
    payload: CreateAssetRequest,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> AssetsForContentPieceResponse:
    """The Creative Brief is resolved through the already-authorized
    Content Piece — never from a client-supplied identifier. No Creative
    Brief -> ``ForbiddenError`` (403), matching the same "dependency
    resource absent" convention used for Tracking Requirement creation
    with no Plan."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    piece_result = ContentService(db).get_piece_detail_for_campaign(
        campaign_id=campaign.id, content_piece_public_id=content_public_id
    )
    if piece_result is None:
        raise ForbiddenError()
    piece, _latest_version = piece_result

    assets_service = AssetsService(db)
    creative_brief = assets_service.creative_briefs.get_for_content_piece(piece.id)
    if creative_brief is None:
        raise ForbiddenError()

    assets_service.record_asset(
        creative_brief=creative_brief,
        kind=payload.kind,
        storage_reference=payload.storage_reference,
        actor_user_id=user.id,
    )
    return _current_assets_response(assets_service, content_piece_id=piece.id)


@router.post(
    "/assets/{asset_public_id}/versions",
    response_model=AssetsForContentPieceResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_asset_version(
    campaign_public_id: str,
    content_public_id: str,
    asset_public_id: str,
    payload: CreateAssetVersionRequest,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> AssetsForContentPieceResponse:
    """MVP-16A §AD: the underlying ``record_asset_version`` service
    method only re-verifies workspace-level tenancy, not that the Asset
    belongs to the specific Content Piece named in this URL — both are
    the same workspace, so this is never a cross-tenant leak, but a
    caller could otherwise append a version to an Asset that belongs to a
    *different* Content Piece in the same workspace. The explicit check
    below closes that URL-scoping gap without touching the service
    itself: an Asset that does not exist, or that exists but is not owned
    by this Content Piece's own Creative Brief, is indistinguishable —
    both are rejected the same non-leaky way."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    piece_result = ContentService(db).get_piece_detail_for_campaign(
        campaign_id=campaign.id, content_piece_public_id=content_public_id
    )
    if piece_result is None:
        raise ForbiddenError()
    piece, _latest_version = piece_result

    assets_service = AssetsService(db)
    creative_brief = assets_service.creative_briefs.get_for_content_piece(piece.id)
    asset = assets_service.assets.get_by_public_id(asset_public_id)
    if creative_brief is None or asset is None or asset.creative_brief_id != creative_brief.id:
        raise ForbiddenError()

    assets_service.record_asset_version(
        workspace_id=workspace.id,
        asset_public_id=asset_public_id,
        storage_reference=payload.storage_reference,
        actor_user_id=user.id,
    )
    return _current_assets_response(assets_service, content_piece_id=piece.id)
