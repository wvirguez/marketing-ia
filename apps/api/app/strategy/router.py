"""Strategy API surface (BACKEND-08 §19) — the canonical read route
`docs/backend/BACKEND-01-API-MAP.md` §2 defines:

    GET /api/v1/campaigns/{campaign_id}/strategy

plus two governed write routes:

    POST /api/v1/campaigns/{campaign_id}/strategy/{strategy_id}/hypotheses
        (MVP-31A/-31A-R1/MVP-31B)
    POST /api/v1/campaigns/{campaign_id}/hypotheses/{hypothesis_id}/experiments
        (MVP-32A/-32A-R1/MVP-32B) — no Strategy in the path: Strategy
        currency is a server-side eligibility check derived exclusively
        from the named Hypothesis's own persisted ``strategy_id`` FK,
        never a client-supplied identifier (MVP-32A-R1 §14).

No other write endpoint exists here — Strategy/Positioning themselves
remain writable only via the deterministic bootstrap
(``app/strategy/service.py::record_strategy``) or the separate governed
Strategy Revision surface (``app/orchestration/strategy_revision_router.py``,
MVP-30B).

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching the same bounded-context separation already applied to
``app/orchestration/router.py``/``app/research/router.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf
from app.campaigns.service import CampaignAccessService
from app.persistence.session import get_db
from app.strategy.schemas import (
    CreateExperimentRequest,
    CreateHypothesisRequest,
    ExperimentPublic,
    HypothesisPublic,
    StrategyOutputResponse,
    experiment_to_public,
    hypothesis_to_public,
    positioning_to_public,
    strategy_to_public,
)
from app.strategy.service import StrategyService
from app.users.models import User
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


@router.post(
    "/strategy/{strategy_public_id}/hypotheses",
    response_model=HypothesisPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_hypothesis(
    campaign_public_id: str,
    strategy_public_id: str,
    payload: CreateHypothesisRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> HypothesisPublic:
    """Records a governed next-cycle Hypothesis under the exact named
    Strategy (MVP-31A/-31A-R1, frozen contract). Any active workspace
    membership may call this (MEMBER+, matching
    ``StrategicImplication``/``StrategicRecommendation``'s own precedent —
    a Hypothesis proposes, it never commits or mutates the authoritative
    Strategy/Positioning state). The named Strategy must still be the
    Campaign's own current version (``HypothesisStrategyStaleError``
    otherwise — never silently rebased to whatever is current now)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    hypothesis = StrategyService(db).create_hypothesis(
        campaign=campaign,
        strategy_public_id=strategy_public_id,
        statement=payload.statement,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return hypothesis_to_public(hypothesis)


@router.post(
    "/hypotheses/{hypothesis_public_id}/experiments",
    response_model=ExperimentPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_experiment(
    campaign_public_id: str,
    hypothesis_public_id: str,
    payload: CreateExperimentRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ExperimentPublic:
    """Records a governed Experiment under the exact named Hypothesis
    (MVP-32A/-32A-R1, frozen contract). Any active workspace membership
    may call this (MEMBER+, matching Hypothesis creation's own precedent —
    an Experiment proposes, it never commits or mutates the authoritative
    Strategy/Positioning/Hypothesis state). Any Hypothesis status is
    eligible (OPEN, CONFIRMED, REFUTED), but the Hypothesis's own parent
    Strategy must still be the Campaign's current version
    (``ExperimentStrategyStaleError`` otherwise — never silently rebased
    to whatever is current now)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    experiment = StrategyService(db).create_experiment(
        campaign=campaign,
        hypothesis_public_id=hypothesis_public_id,
        description=payload.description,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return experiment_to_public(experiment, hypothesis_public_id=hypothesis_public_id)
