"""Strategy API surface (BACKEND-08 §19) — the exact, canonical, GET-only
route `docs/backend/BACKEND-01-API-MAP.md` §2 defines:

    GET /api/v1/campaigns/{campaign_id}/strategy

No write endpoint exists here at all — BACKEND-01's own API map marks this
route GET-only ("Experiments may get a POST later" — future, not now), and
nothing in this stage has a legitimate trigger to create strategy output
yet (no Agent Run/Gate Decision exists). Writes are service-layer only
(``app/strategy/service.py``).

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching the same bounded-context separation already applied to
``app/orchestration/router.py``/``app/research/router.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_workspace
from app.campaigns.service import CampaignAccessService
from app.persistence.session import get_db
from app.strategy.schemas import (
    StrategyOutputResponse,
    experiment_to_public,
    hypothesis_to_public,
    positioning_to_public,
    strategy_to_public,
)
from app.strategy.service import StrategyService
from app.workspaces.models import Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}", tags=["strategy"])


@router.get("/strategy", response_model=StrategyOutputResponse)
async def get_strategy(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategyOutputResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    strategy, positioning, hypotheses, experiments = StrategyService(db).get_strategy_output(campaign=campaign)
    hypothesis_public_id_by_id = {h.id: h.public_id for h in hypotheses}
    return StrategyOutputResponse(
        strategy=strategy_to_public(strategy, campaign_public_id=campaign.public_id) if strategy else None,
        positioning=positioning_to_public(positioning) if positioning else None,
        hypotheses=[hypothesis_to_public(h) for h in hypotheses],
        experiments=[
            experiment_to_public(e, hypothesis_public_id=hypothesis_public_id_by_id[e.hypothesis_id])
            for e in experiments
        ],
    )
