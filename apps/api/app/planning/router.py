"""Planning API surface (BACKEND-09 §18) — the exact, canonical, GET-only
route `docs/backend/BACKEND-01-API-MAP.md` §2 defines:

    GET /api/v1/campaigns/{campaign_id}/plan

No write endpoint exists here at all — BACKEND-01's own API map marks this
route GET-only, and nothing in this stage has a legitimate trigger to create
plan output yet (no Agent Run/Gate Decision exists). Writes are service-layer
only (``app/planning/service.py``).

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching the same bounded-context separation already applied to
``app/strategy/router.py``/``app/research/router.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_workspace
from app.campaigns.service import CampaignAccessService
from app.persistence.session import get_db
from app.planning.schemas import PlanOutputResponse, content_plan_to_public, plan_item_to_public
from app.planning.service import PlanningService
from app.workspaces.models import Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}", tags=["planning"])


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
        plan=content_plan_to_public(plan, campaign_public_id=campaign.public_id) if plan else None,
        items=[plan_item_to_public(item) for item in items],
    )
