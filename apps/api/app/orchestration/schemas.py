"""Public DTOs for the orchestration domain. Never expose an internal
UUID or an internal ``AGENT-0N`` identifier — only public_id-derived
fields and business-language stage names (BACKEND-06 §7/§26)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.orchestration.models import HumanDecisionRequest, HumanDecisionResponse, RunStageExecution


class StageExecutionPublic(BaseModel):
    id: str
    stage: str
    ordinal: int
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    blocked_reason: str | None
    failure_reason: str | None


class StageExecutionListResponse(BaseModel):
    items: list[StageExecutionPublic]


class RunProgressPublic(BaseModel):
    """Business-oriented projection only (BACKEND-06 §26) — no internal
    UUIDs, no AGENT-0N identifiers, no chain-of-thought, no percentage
    (BACKEND-01 defines no percentage formula, so none is fabricated
    here)."""

    campaign_id: str
    run_id: str
    run_status: str
    current_stage: str | None
    stages: list[StageExecutionPublic]
    waiting_for_input: bool
    open_decision_count: int


class DecisionResponsePublic(BaseModel):
    id: str
    response_text: str
    responded_by_user_id: str
    created_at: datetime


class DecisionRequestPublic(BaseModel):
    id: str
    stage: str | None
    question: str
    status: str
    created_at: datetime
    resolved_at: datetime | None
    response: DecisionResponsePublic | None


class DecisionRequestListResponse(BaseModel):
    items: list[DecisionRequestPublic]
    limit: int
    offset: int
    total: int


class DecisionRespondRequest(BaseModel):
    response_text: str = Field(min_length=1, max_length=2000)


def stage_execution_to_public(stage_execution: RunStageExecution) -> StageExecutionPublic:
    return StageExecutionPublic(
        id=stage_execution.public_id,
        stage=stage_execution.stage.value,
        ordinal=stage_execution.ordinal,
        status=stage_execution.status.value,
        started_at=stage_execution.started_at,
        completed_at=stage_execution.completed_at,
        blocked_reason=stage_execution.blocked_reason,
        failure_reason=stage_execution.failure_reason,
    )


def decision_response_to_public(response: HumanDecisionResponse, *, responder_public_id: str) -> DecisionResponsePublic:
    return DecisionResponsePublic(
        id=response.public_id,
        response_text=response.response_text,
        responded_by_user_id=responder_public_id,
        created_at=response.created_at,
    )


def decision_request_to_public(
    request: HumanDecisionRequest,
    *,
    stage: str | None,
    response: DecisionResponsePublic | None,
) -> DecisionRequestPublic:
    return DecisionRequestPublic(
        id=request.public_id,
        stage=stage,
        question=request.question,
        status=request.status.value,
        created_at=request.created_at,
        resolved_at=request.resolved_at,
        response=response,
    )
