"""StrategicApproval API surface — MVP-29B (frozen MVP-29A contract) —
exactly:

    GET  /api/v1/campaigns/{campaign_id}/strategic-decisions/{decision_id}/approval
    POST /api/v1/campaigns/{campaign_id}/strategic-decisions/{decision_id}/approval
    GET  /api/v1/campaigns/{campaign_id}/strategic-approvals

No generic PATCH/DELETE, no `/approvals/{approval_id}` mutation route —
StrategicApproval is strictly insert-only after creation
(``app/orchestration/models.py::StrategicApproval``); there is nothing to
mutate.

Every route remains campaign-scoped (MVP-29B §19): none of these resolve
a StrategicDecision or StrategicApproval outside the authenticated
caller's own Campaign/Workspace membership.

Authority (MVP-29A §H, frozen by direct precedent from StrategicDecision's
own record/supersede routes): the write route requires ``OWNER``/``ADMIN``
(``require_role``) plus CSRF — a StrategicApproval is at least as
governance-weighted as the StrategicDecision it ratifies. Reads require
only active campaign/workspace membership.

Mounted directly on ``api_v1_router``, matching
``app/orchestration/strategic_decision_router.py``'s own bounded-context
separation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf, require_role
from app.campaigns.service import CampaignAccessService
from app.core.api_errors import ForbiddenError
from app.orchestration.models import StrategicApproval, StrategicDecision
from app.orchestration.service import StrategicApprovalService, StrategicDecisionService
from app.orchestration.strategic_approval_schemas import (
    RecordStrategicApprovalRequest,
    StrategicApprovalListResponse,
    StrategicApprovalPublic,
    strategic_approval_to_public,
)
from app.persistence.session import get_db
from app.users.models import User
from app.workspaces.models import MembershipRole, Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}", tags=["strategic-approvals"])


def _decision_public_ids(db: Session, approvals: list[StrategicApproval]) -> dict:
    """Batched lookup, never one query per row — mirrors
    ``app/orchestration/strategic_decision_router.py``'s own
    N+1-avoiding ``_recommendation_public_ids`` helper exactly."""
    ids = {a.strategic_decision_id for a in approvals}
    result: dict = {}
    for decision_id in ids:
        decision = db.get(StrategicDecision, decision_id)
        result[decision_id] = decision.public_id if decision else None
    return result


@router.get(
    "/strategic-decisions/{decision_public_id}/approval",
    response_model=StrategicApprovalPublic | None,
)
async def get_strategic_approval_for_decision(
    campaign_public_id: str,
    decision_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategicApprovalPublic | None:
    """Returns ``null`` when the Decision exists (and is accessible) but
    has no Approval yet — a legitimate, expected state (MVP-29A §W legacy
    compatibility), never conflated with the Decision itself not existing
    or not being accessible (``ForbiddenError``, non-leaky, MVP-29B §8).
    Always scoped to this exact ``decision_public_id`` — a superseded
    Decision's own historical Approval is never returned for a different,
    current Decision (MVP-29B §22)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    decision = StrategicDecisionService(db).get_decision_for_campaign(
        campaign=campaign, decision_public_id=decision_public_id
    )
    if decision is None:
        raise ForbiddenError()
    approval = StrategicApprovalService(db).get_approval_for_decision(decision_id=decision.id)
    if approval is None:
        return None
    return strategic_approval_to_public(
        approval, campaign_public_id=campaign.public_id, decision_public_id=decision.public_id
    )


@router.post(
    "/strategic-decisions/{decision_public_id}/approval",
    response_model=StrategicApprovalPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(require_role(MembershipRole.OWNER, MembershipRole.ADMIN))],
)
async def record_strategic_approval(
    campaign_public_id: str,
    decision_public_id: str,
    payload: RecordStrategicApprovalRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategicApprovalPublic:
    """Records the one-shot terminal outcome (APPROVED/REJECTED) for one
    currently-ADOPT, currently-current StrategicDecision
    (``StrategicDecisionNotEligibleForApprovalError`` otherwise) that does
    not already have an Approval (``StrategicApprovalAlreadyExistsError``
    otherwise — never a second, never a replacement)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    approval = StrategicApprovalService(db).record_approval(
        campaign=campaign,
        decision_public_id=decision_public_id,
        outcome=payload.outcome,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return strategic_approval_to_public(
        approval, campaign_public_id=campaign.public_id, decision_public_id=decision_public_id
    )


@router.get("/strategic-approvals", response_model=StrategicApprovalListResponse)
async def list_strategic_approvals(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategicApprovalListResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    approvals = StrategicApprovalService(db).list_approvals_for_campaign(campaign.id)
    decision_public_ids = _decision_public_ids(db, approvals)
    return StrategicApprovalListResponse(
        items=[
            strategic_approval_to_public(
                approval,
                campaign_public_id=campaign.public_id,
                decision_public_id=decision_public_ids.get(approval.strategic_decision_id),
            )
            for approval in approvals
        ]
    )
