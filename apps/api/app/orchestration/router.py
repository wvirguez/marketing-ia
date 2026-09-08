"""Orchestration API surface (BACKEND-06 §25). Mounted directly on
``api_v1_router`` (not nested inside the campaigns router) with its own
full path prefix, matching BACKEND-01's bounded-context separation of
``orchestration`` from ``campaigns`` (§9) while reusing the same URL
namespace BACKEND-05 already established for the run list endpoint.

Every route depends on ``get_current_workspace`` (session-derived only)
and resolves the specific run through
``CampaignAccessService.get_authorized_run`` — never a client-supplied
workspace/campaign/run id trusted on its own. Every mutation additionally
depends on ``require_csrf``. No route here creates a Human Decision
Request (BACKEND-06 §19) — only reading and responding to one that
already exists.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.audit.repository import AuditEventRepository
from app.audit.schemas import AuditEventListResponse, audit_event_to_public
from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf
from app.campaigns.models import Campaign, CampaignRun, CampaignRunStatus
from app.campaigns.schemas import CampaignRunPublic, campaign_run_to_public
from app.campaigns.service import CampaignAccessService
from app.orchestration.repository import HumanDecisionRequestRepository, RunStageExecutionRepository
from app.orchestration.schemas import (
    DecisionRequestListResponse,
    DecisionRespondRequest,
    DecisionResponsePublic,
    RunProgressPublic,
    StageExecutionListResponse,
    decision_request_to_public,
    decision_response_to_public,
    stage_execution_to_public,
)
from app.orchestration.service import OrchestrationService
from app.persistence.session import get_db
from app.users.models import User
from app.users.repository import UserRepository
from app.workspaces.models import Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}/runs/{run_public_id}", tags=["orchestration"])

_DEFAULT_PAGE_LIMIT = 20
_MAX_PAGE_LIMIT = 100


def _authorized_run(
    campaign_public_id: str, run_public_id: str, workspace: Workspace, db: Session, *, for_update: bool = False
) -> tuple[Campaign, CampaignRun]:
    return CampaignAccessService(db).get_authorized_run(
        workspace_id=workspace.id,
        campaign_public_id=campaign_public_id,
        run_public_id=run_public_id,
        for_update=for_update,
    )


@router.post("/initialize", response_model=StageExecutionListResponse, dependencies=[Depends(require_csrf)])
async def initialize_run(
    campaign_public_id: str,
    run_public_id: str,
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StageExecutionListResponse:
    campaign, run = _authorized_run(campaign_public_id, run_public_id, workspace, db, for_update=True)
    stages = OrchestrationService(db).initialize_run(
        campaign=campaign, run=run, actor_user_id=user.id, request_id=request.state.request_id
    )
    return StageExecutionListResponse(items=[stage_execution_to_public(s) for s in stages])


@router.post("/start", response_model=CampaignRunPublic, dependencies=[Depends(require_csrf)])
async def start_run(
    campaign_public_id: str,
    run_public_id: str,
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignRunPublic:
    campaign, run = _authorized_run(campaign_public_id, run_public_id, workspace, db, for_update=True)
    run = OrchestrationService(db).start_run(
        campaign=campaign, run=run, actor_user_id=user.id, request_id=request.state.request_id
    )
    return campaign_run_to_public(run, campaign_public_id=campaign.public_id)


@router.get("", response_model=CampaignRunPublic)
async def get_run(
    campaign_public_id: str,
    run_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CampaignRunPublic:
    campaign, run = _authorized_run(campaign_public_id, run_public_id, workspace, db)
    return campaign_run_to_public(run, campaign_public_id=campaign.public_id)


@router.get("/progress", response_model=RunProgressPublic)
async def get_progress(
    campaign_public_id: str,
    run_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> RunProgressPublic:
    campaign, run = _authorized_run(campaign_public_id, run_public_id, workspace, db)
    stages, current_stage, open_count = OrchestrationService(db).get_progress(campaign=campaign, run=run)
    return RunProgressPublic(
        campaign_id=campaign.public_id,
        run_id=run.public_id,
        run_status=run.status.value,
        current_stage=current_stage,
        stages=[stage_execution_to_public(s) for s in stages],
        waiting_for_input=run.status is CampaignRunStatus.AWAITING_HUMAN_DECISION,
        open_decision_count=open_count,
    )


@router.get("/stages", response_model=StageExecutionListResponse)
async def list_stages(
    campaign_public_id: str,
    run_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StageExecutionListResponse:
    _campaign, run = _authorized_run(campaign_public_id, run_public_id, workspace, db)
    stages = OrchestrationService(db).list_stages(run=run)
    return StageExecutionListResponse(items=[stage_execution_to_public(s) for s in stages])


@router.get("/events", response_model=AuditEventListResponse)
async def list_events(
    campaign_public_id: str,
    run_public_id: str,
    limit: int = Query(default=_DEFAULT_PAGE_LIMIT, ge=1, le=_MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> AuditEventListResponse:
    _campaign, run = _authorized_run(campaign_public_id, run_public_id, workspace, db)
    events, total = AuditEventRepository(db).list_for_run(campaign_run_id=run.id, limit=limit, offset=offset)

    users = UserRepository(db)
    decision_requests = HumanDecisionRequestRepository(db)
    actor_public_ids: dict = {}
    decision_public_ids: dict = {}
    items = []
    for event in events:
        actor_public_id = None
        if event.actor_user_id is not None:
            if event.actor_user_id not in actor_public_ids:
                actor = users.get_by_id(event.actor_user_id)
                actor_public_ids[event.actor_user_id] = actor.public_id if actor else None
            actor_public_id = actor_public_ids[event.actor_user_id]

        decision_public_id = None
        if event.decision_request_id is not None:
            if event.decision_request_id not in decision_public_ids:
                decision_request = decision_requests.get_by_id(event.decision_request_id)
                decision_public_ids[event.decision_request_id] = decision_request.public_id if decision_request else None
            decision_public_id = decision_public_ids[event.decision_request_id]

        items.append(
            audit_event_to_public(event, actor_public_id=actor_public_id, decision_public_id=decision_public_id)
        )

    return AuditEventListResponse(items=items, limit=limit, offset=offset, total=total)


@router.get("/decisions", response_model=DecisionRequestListResponse)
async def list_decisions(
    campaign_public_id: str,
    run_public_id: str,
    limit: int = Query(default=_DEFAULT_PAGE_LIMIT, ge=1, le=_MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> DecisionRequestListResponse:
    _campaign, run = _authorized_run(campaign_public_id, run_public_id, workspace, db)
    service = OrchestrationService(db)
    requests, total = service.list_decisions(run=run, limit=limit, offset=offset)
    stages = RunStageExecutionRepository(db)
    users = UserRepository(db)

    items = []
    for decision_request in requests:
        stage_name = None
        if decision_request.stage_execution_id is not None:
            stage_execution = stages.get_by_id(decision_request.stage_execution_id)
            stage_name = stage_execution.stage.value if stage_execution else None

        response_public = None
        response = service.get_response_for_request(decision_request_id=decision_request.id)
        if response is not None:
            responder = users.get_by_id(response.responded_by_user_id)
            response_public = decision_response_to_public(
                response, responder_public_id=responder.public_id if responder else ""
            )

        items.append(decision_request_to_public(decision_request, stage=stage_name, response=response_public))

    return DecisionRequestListResponse(items=items, limit=limit, offset=offset, total=total)


@router.post(
    "/decisions/{decision_public_id}/respond",
    response_model=DecisionResponsePublic,
    dependencies=[Depends(require_csrf)],
)
async def respond_to_decision(
    campaign_public_id: str,
    run_public_id: str,
    decision_public_id: str,
    payload: DecisionRespondRequest,
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DecisionResponsePublic:
    campaign, run = _authorized_run(campaign_public_id, run_public_id, workspace, db, for_update=True)
    response = OrchestrationService(db).respond_to_decision(
        campaign=campaign,
        run=run,
        decision_public_id=decision_public_id,
        responder_user_id=user.id,
        response_text=payload.response_text,
        request_id=request.state.request_id,
    )
    return decision_response_to_public(response, responder_public_id=user.public_id)
