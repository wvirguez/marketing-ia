"""StrategicDecision API surface — MVP-28B (frozen MVP-28A/-R1/-R2
contract) — exactly:

    GET  /api/v1/campaigns/{campaign_id}/strategic-decisions
    GET  /api/v1/campaigns/{campaign_id}/strategic-decisions/{decision_id}
    POST /api/v1/campaigns/{campaign_id}/strategic-decisions
    POST /api/v1/campaigns/{campaign_id}/strategic-decisions/{decision_id}/supersede

No generic PATCH/DELETE — DECISION CONTENT is immutable after insert
(``app/orchestration/models.py::StrategicDecision``); the only permitted
mutation (supersession metadata) happens exclusively inside the dedicated
``/supersede`` action, never through a general-purpose update path.

Authority (MVP-28A-R2 §K, frozen by direct precedent from Recommendation
decision / Content Approval — both real governed-decision-weight actions
in this codebase, unlike Commercial's own lower-weight Objective/Offer
record-keeping): both write routes require ``OWNER``/``ADMIN``
(``require_role``) — a StrategicDecision is a higher-order governance fact,
not a routine workflow record. Reads require only active campaign/
workspace membership. Every write additionally requires CSRF.

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching the same bounded-context separation already applied to
``app/commercial/router.py``/``app/learning/router.py`` — this router's own
prefix already includes ``/campaigns/{campaign_public_id}``, distinct from
``app/orchestration/router.py``'s own run-scoped prefix one level deeper.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf, require_role
from app.campaigns.service import CampaignAccessService
from app.core.api_errors import ForbiddenError
from app.learning.models import StrategicRecommendationCandidate
from app.orchestration.models import StrategicDecision
from app.orchestration.service import StrategicDecisionService
from app.orchestration.strategic_decision_schemas import (
    RecordStrategicDecisionRequest,
    StrategicDecisionListResponse,
    StrategicDecisionPublic,
    SupersedeStrategicDecisionRequest,
    strategic_decision_to_public,
)
from app.persistence.session import get_db
from app.users.models import User
from app.workspaces.models import MembershipRole, Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}", tags=["strategic-decisions"])


def _recommendation_public_ids(db: Session, decisions: list[StrategicDecision]) -> dict:
    """Batched lookup, never one query per row (mirrors
    ``app/commercial/router.py``'s own N+1-avoiding cross-reference
    pattern) — resolves every distinct
    ``strategic_recommendation_candidate_id`` referenced by ``decisions``
    to its public_id in a single pass."""
    ids = {d.strategic_recommendation_candidate_id for d in decisions if d.strategic_recommendation_candidate_id}
    result: dict = {}
    for recommendation_id in ids:
        recommendation = db.get(StrategicRecommendationCandidate, recommendation_id)
        result[recommendation_id] = recommendation.public_id if recommendation else None
    return result


def _decisions_to_public(db: Session, decisions: list[StrategicDecision], *, campaign_public_id: str) -> list[StrategicDecisionPublic]:
    public_id_by_id = {d.id: d.public_id for d in decisions}
    recommendation_public_ids = _recommendation_public_ids(db, decisions)
    return [
        strategic_decision_to_public(
            decision,
            campaign_public_id=campaign_public_id,
            recommendation_public_id=recommendation_public_ids.get(decision.strategic_recommendation_candidate_id),
            superseded_by_public_id=public_id_by_id.get(decision.superseded_by_strategic_decision_id),
        )
        for decision in decisions
    ]


@router.get("/strategic-decisions", response_model=StrategicDecisionListResponse)
async def list_strategic_decisions(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategicDecisionListResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    decisions = StrategicDecisionService(db).list_decisions_for_campaign(campaign.id)
    return StrategicDecisionListResponse(items=_decisions_to_public(db, decisions, campaign_public_id=campaign.public_id))


@router.get("/strategic-decisions/{decision_public_id}", response_model=StrategicDecisionPublic)
async def get_strategic_decision(
    campaign_public_id: str,
    decision_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategicDecisionPublic:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    decision = StrategicDecisionService(db).get_decision_for_campaign(
        campaign=campaign, decision_public_id=decision_public_id
    )
    if decision is None:
        raise ForbiddenError()
    return _decisions_to_public(db, [decision], campaign_public_id=campaign.public_id)[0]


@router.post(
    "/strategic-decisions",
    response_model=StrategicDecisionPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(require_role(MembershipRole.OWNER, MembershipRole.ADMIN))],
)
async def record_strategic_decision(
    campaign_public_id: str,
    payload: RecordStrategicDecisionRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategicDecisionPublic:
    """Records the first (or a subsequent, independent-of-supersession)
    StrategicDecision for one accepted StrategicRecommendationCandidate.
    Requires the Recommendation's own ``decision`` to already be ACCEPTED
    (``StrategicRecommendationNotAcceptedError`` otherwise) and that no
    current Decision already exists for it
    (``StrategicDecisionAlreadyExistsError`` otherwise — supersede the
    existing one instead)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    decision = StrategicDecisionService(db).record_decision(
        campaign=campaign,
        recommendation_public_id=payload.strategic_recommendation_candidate_id,
        decision_type=payload.decision_type,
        statement=payload.statement,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    recommendation = db.get(StrategicRecommendationCandidate, decision.strategic_recommendation_candidate_id)
    return strategic_decision_to_public(
        decision,
        campaign_public_id=campaign.public_id,
        recommendation_public_id=recommendation.public_id if recommendation else None,
    )


@router.post(
    "/strategic-decisions/{decision_public_id}/supersede",
    response_model=StrategicDecisionPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(require_role(MembershipRole.OWNER, MembershipRole.ADMIN))],
)
async def supersede_strategic_decision(
    campaign_public_id: str,
    decision_public_id: str,
    payload: SupersedeStrategicDecisionRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategicDecisionPublic:
    """Atomic supersession: locks the specific original Decision row,
    creates a fresh replacement referencing the *original's own*
    Recommendation, marks the original historical. A second attempt against
    an already-superseded original deterministically 409s
    (``StrategicDecisionAlreadySupersededError``) — never a silent second
    replacement."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    replacement = StrategicDecisionService(db).supersede_decision(
        campaign=campaign,
        decision_public_id=decision_public_id,
        decision_type=payload.decision_type,
        statement=payload.statement,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    recommendation = db.get(StrategicRecommendationCandidate, replacement.strategic_recommendation_candidate_id)
    return strategic_decision_to_public(
        replacement,
        campaign_public_id=campaign.public_id,
        recommendation_public_id=recommendation.public_id if recommendation else None,
    )
