"""Governed Strategy Revision API surface — MVP-30B (frozen
MVP-30A/-30A-R1 contract) — exactly:

    POST /api/v1/campaigns/{campaign_id}/strategy/{base_strategy_id}/revision
    GET  /api/v1/campaigns/{campaign_id}/strategy/history

No generic PATCH/DELETE, no `/revisions/{id}` mutation route —
StrategyRevision is strictly insert-only after creation
(``app/orchestration/models.py::StrategyRevision``); there is nothing to
mutate. No global, non-campaign-scoped revision route exists.

Authority (MVP-30A §E, frozen by direct precedent from StrategicDecision's
own record/supersede routes and StrategicApproval's own record route): the
write route requires ``OWNER``/``ADMIN`` (``require_role``) plus CSRF — a
governed Strategy Revision is the most consequential write in this entire
governance chain (it is the first artifact that actually mutates the
operative Strategy). Reads require only active campaign/workspace
membership.

Mounted directly on ``api_v1_router``, matching
``app/orchestration/strategic_approval_router.py``'s own bounded-context
separation. Deliberately a separate router from ``app/strategy/router.py``
(which keeps its own unchanged, GET-only current-Strategy route) — the
dependency direction here is orchestration -> app/strategy/, the same
direction the deterministic bootstrap already uses, never the reverse.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf, require_role
from app.campaigns.service import CampaignAccessService
from app.orchestration.models import StrategicApproval
from app.orchestration.service import StrategyRevisionService
from app.orchestration.strategy_revision_schemas import (
    RecordStrategyRevisionRequest,
    StrategyHistoryItem,
    StrategyHistoryResponse,
    StrategyRevisionResult,
    strategy_revision_to_public,
)
from app.persistence.session import get_db
from app.strategy.schemas import positioning_to_public, strategy_to_public
from app.strategy.service import StrategyService
from app.users.models import User
from app.workspaces.models import MembershipRole, Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}", tags=["strategy-revisions"])


@router.post(
    "/strategy/{base_strategy_public_id}/revision",
    response_model=StrategyRevisionResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(require_role(MembershipRole.OWNER, MembershipRole.ADMIN))],
)
async def revise_strategy(
    campaign_public_id: str,
    base_strategy_public_id: str,
    payload: RecordStrategyRevisionRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategyRevisionResult:
    """Records a governed Strategy Revision: authorizes and applies a
    complete new Strategy + Positioning state, atomically, against the
    exact named base Strategy, consuming the exact named (unconsumed,
    APPROVED, currently-ADOPT-Decision-backed) StrategicApproval. Requires
    the base Strategy to still be the Campaign's own current version
    (``StrategyRevisionBaseStaleError`` otherwise — never silently
    rebased) and the Approval to be eligible and unconsumed
    (``StrategyRevisionNotEligibleError`` /
    ``StrategicApprovalAlreadyConsumedError`` otherwise)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    strategy, positioning, revision = StrategyRevisionService(db).revise_strategy(
        campaign=campaign,
        base_strategy_public_id=base_strategy_public_id,
        strategic_approval_public_id=payload.strategic_approval_id,
        summary=payload.summary,
        positioning_statement=payload.positioning_statement,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return StrategyRevisionResult(
        strategy=strategy_to_public(strategy, campaign_public_id=campaign.public_id),
        positioning=positioning_to_public(positioning),
        revision=strategy_revision_to_public(
            revision,
            campaign_public_id=campaign.public_id,
            strategic_approval_public_id=payload.strategic_approval_id,
            base_strategy_public_id=base_strategy_public_id,
            result_strategy_public_id=strategy.public_id,
        ),
    )


@router.get("/strategy/history", response_model=StrategyHistoryResponse)
async def get_strategy_history(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategyHistoryResponse:
    """Every Strategy version for the Campaign, oldest first, each paired
    with its own StrategyRevision provenance where one exists (``None`` for
    BOOTSTRAP-origin/legacy versions, MVP-30A-R1 §AG — never fabricated).
    Minimum necessary observability (MVP-30A §Y) — no broader reporting
    surface."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    strategies = StrategyService(db).list_versions_for_campaign(campaign.id)
    revisions = StrategyRevisionService(db).list_revisions_for_campaign(campaign.id)

    public_id_by_strategy_id = {s.id: s.public_id for s in strategies}
    approval_ids = {r.strategic_approval_id for r in revisions}
    approval_public_id_by_id = {
        approval_id: (approval.public_id if (approval := db.get(StrategicApproval, approval_id)) else None)
        for approval_id in approval_ids
    }
    revision_by_result_strategy_id = {r.result_strategy_id: r for r in revisions}

    items: list[StrategyHistoryItem] = []
    for strategy in strategies:
        revision = revision_by_result_strategy_id.get(strategy.id)
        revision_public = None
        if revision is not None:
            revision_public = strategy_revision_to_public(
                revision,
                campaign_public_id=campaign.public_id,
                strategic_approval_public_id=approval_public_id_by_id.get(revision.strategic_approval_id) or "",
                base_strategy_public_id=public_id_by_strategy_id.get(revision.base_strategy_id) or "",
                result_strategy_public_id=strategy.public_id,
            )
        items.append(
            StrategyHistoryItem(
                strategy=strategy_to_public(strategy, campaign_public_id=campaign.public_id),
                revision=revision_public,
            )
        )
    return StrategyHistoryResponse(items=items)
