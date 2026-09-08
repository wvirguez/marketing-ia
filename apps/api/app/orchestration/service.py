"""Orchestration lifecycle operations — BACKEND-06.

Every public method here follows the same transaction-ownership pattern
established in BACKEND-04/05 (``AuthService``, ``CampaignService``):
load/validate, mutate, record exactly one Audit Event for the change,
call ``self.session.commit()`` once at the end. If anything raises
before that commit — including the Audit Event's own ``flush()`` —
nothing persists; the state transition and its traceability record can
never diverge (BACKEND-06 §28).

ORCHESTRATION FOUNDATION != AI EXECUTION. Nothing below calls an AI
provider, invokes an agent, starts a worker, or produces a generated
payload — every operation here is a validated, audited PostgreSQL
state transition and nothing else.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign, CampaignRun, CampaignRunStatus
from app.core.api_errors import (
    DecisionAlreadyResolvedError,
    ForbiddenError,
    InvalidLifecycleTransitionError,
    OrchestrationNotInitializedError,
)
from app.orchestration.models import (
    DecisionRequestStatus,
    HumanDecisionRequest,
    HumanDecisionResponse,
    RunStageExecution,
    StageExecutionStatus,
)
from app.orchestration.repository import (
    HumanDecisionRequestRepository,
    HumanDecisionResponseRepository,
    RunStageExecutionRepository,
)
from app.orchestration.transitions import is_legal_run_transition, is_legal_stage_transition

EVENT_ORCHESTRATION_INITIALIZED = "orchestration.initialized"
EVENT_RUN_TRANSITIONED = "orchestration.run.transitioned"
EVENT_STAGE_TRANSITIONED = "orchestration.stage.transitioned"
EVENT_DECISION_OPENED = "orchestration.decision.opened"
EVENT_DECISION_RESOLVED = "orchestration.decision.resolved"

_FIRST_STAGE_ORDINAL = 1


class OrchestrationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.stages = RunStageExecutionRepository(session)
        self.decision_requests = HumanDecisionRequestRepository(session)
        self.decision_responses = HumanDecisionResponseRepository(session)
        self.events = AuditEventRepository(session)

    # --- internal transition helpers, each pairs a mutation with exactly
    # one Audit Event in the same (uncommitted) transaction -------------

    def _transition_run(
        self,
        *,
        run: CampaignRun,
        target: CampaignRunStatus,
        campaign_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request_id: str | None,
    ) -> None:
        if not is_legal_run_transition(run.status, target):
            raise InvalidLifecycleTransitionError(
                f"Cannot transition a run from {run.status.value} to {target.value}."
            )
        previous = run.status
        run.status = target
        self.events.record(
            workspace_id=run.workspace_id,
            event_type=EVENT_RUN_TRANSITIONED,
            actor_type=ActorType.USER,
            campaign_id=campaign_id,
            campaign_run_id=run.id,
            previous_state=previous.value,
            new_state=target.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )

    def _transition_stage(
        self,
        *,
        stage_execution: RunStageExecution,
        target: StageExecutionStatus,
        campaign_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request_id: str | None,
    ) -> None:
        if not is_legal_stage_transition(stage_execution.status, target):
            raise InvalidLifecycleTransitionError(
                f"Cannot transition stage {stage_execution.stage.value} from "
                f"{stage_execution.status.value} to {target.value}."
            )
        previous = stage_execution.status
        stage_execution.status = target
        now = datetime.now(timezone.utc)
        if target == StageExecutionStatus.RUNNING and stage_execution.started_at is None:
            stage_execution.started_at = now
        if target in (StageExecutionStatus.COMPLETED, StageExecutionStatus.FAILED):
            stage_execution.completed_at = now
        self.events.record(
            workspace_id=stage_execution.workspace_id,
            event_type=EVENT_STAGE_TRANSITIONED,
            actor_type=ActorType.USER,
            campaign_id=campaign_id,
            campaign_run_id=stage_execution.campaign_run_id,
            stage_execution_id=stage_execution.id,
            previous_state=previous.value,
            new_state=target.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )

    # --- initialization / start -----------------------------------------

    def initialize_run(
        self, *, campaign: Campaign, run: CampaignRun, actor_user_id: uuid.UUID, request_id: str | None
    ) -> list[RunStageExecution]:
        """Idempotent (BACKEND-06 §30): if this run's stages are already
        materialized, returns them as-is rather than erroring or
        duplicating — repeated client retries of the same "get ready"
        intent are safe. Only the *first* call performs any write."""
        existing = self.stages.list_for_run(campaign_run_id=run.id)
        if existing:
            return existing

        if run.status is not CampaignRunStatus.CREATED:
            raise InvalidLifecycleTransitionError("Only a CREATED run can be initialized.")

        stages = self.stages.materialize_for_run(campaign_run=run)
        self.events.record(
            workspace_id=run.workspace_id,
            event_type=EVENT_ORCHESTRATION_INITIALIZED,
            actor_type=ActorType.USER,
            campaign_id=campaign.id,
            campaign_run_id=run.id,
            new_state=f"{len(stages)}_stages_materialized",
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return stages

    def start_run(
        self, *, campaign: Campaign, run: CampaignRun, actor_user_id: uuid.UUID, request_id: str | None
    ) -> CampaignRun:
        """Not idempotent on purpose (BACKEND-06 §30): starting is a
        meaningful, one-time transition (CREATED -> RUNNING); a second
        call is a deterministic conflict (``INVALID_LIFECYCLE_TRANSITION``),
        matching every other lifecycle-changing operation in this
        service — only ``initialize_run`` gets idempotent-return-current-
        state treatment, since materializing the same stage set twice has
        no meaningful "second time" semantics to protect."""
        stages = self.stages.list_for_run(campaign_run_id=run.id)
        if not stages:
            raise OrchestrationNotInitializedError()

        self._transition_run(
            run=run,
            target=CampaignRunStatus.RUNNING,
            campaign_id=campaign.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )

        first_stage = self.stages.get_by_ordinal_for_update(campaign_run_id=run.id, ordinal=_FIRST_STAGE_ORDINAL)
        if first_stage is not None and first_stage.status is StageExecutionStatus.PENDING:
            # Only ever promoted to READY — "next up," not "running."
            # Starting a run never means an agent actually ran
            # (BACKEND-06 §15): nothing here marks any stage RUNNING or
            # COMPLETED.
            self._transition_stage(
                stage_execution=first_stage,
                target=StageExecutionStatus.READY,
                campaign_id=campaign.id,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )

        self.session.commit()
        return run

    # --- reads -------------------------------------------------------------

    def list_stages(self, *, run: CampaignRun) -> list[RunStageExecution]:
        return self.stages.list_for_run(campaign_run_id=run.id)

    def get_progress(self, *, campaign: Campaign, run: CampaignRun) -> tuple[list[RunStageExecution], str | None, int]:
        stages = self.stages.list_for_run(campaign_run_id=run.id)
        terminal = {
            StageExecutionStatus.COMPLETED,
            StageExecutionStatus.FAILED,
            StageExecutionStatus.SKIPPED,
            StageExecutionStatus.CANCELLED,
        }
        current_stage = next((s.stage.value for s in stages if s.status not in terminal), None)
        open_decisions, _ = self.decision_requests.list_for_run(campaign_run_id=run.id, limit=1000, offset=0)
        open_count = sum(1 for d in open_decisions if d.status is DecisionRequestStatus.OPEN)
        return stages, current_stage, open_count

    def list_decisions(
        self, *, run: CampaignRun, limit: int, offset: int
    ) -> tuple[list[HumanDecisionRequest], int]:
        return self.decision_requests.list_for_run(campaign_run_id=run.id, limit=limit, offset=offset)

    def get_response_for_request(self, *, decision_request_id: uuid.UUID) -> HumanDecisionResponse | None:
        return self.decision_responses.get_for_request(decision_request_id)

    # --- human-in-the-loop -----------------------------------------------

    def create_decision_request(
        self,
        *,
        campaign: Campaign,
        run: CampaignRun,
        question: str,
        stage_execution_id: uuid.UUID | None,
        actor_user_id: uuid.UUID,
        request_id: str | None,
    ) -> HumanDecisionRequest:
        """Service-only — BACKEND-06 exposes no public HTTP endpoint to
        create a decision request (§19): nothing in this stage's
        supported flows legitimately needs to raise one yet (no agent
        exists to escalate a question). This exists as the controlled
        domain mechanism tests use to construct a fixture, and the entry
        point a future runtime phase (BACKEND-07+) will call for real.
        Forces the run into ``AWAITING_HUMAN_DECISION``, matching
        BACKEND-01 §4: "An OPEN Human Decision Request forces
        Orchestration Run -> AWAITING_HUMAN_DECISION"."""
        if run.status is not CampaignRunStatus.RUNNING:
            raise InvalidLifecycleTransitionError("A decision can only be raised while the run is RUNNING.")

        request = self.decision_requests.create(campaign_run=run, question=question, stage_execution_id=stage_execution_id)
        self._transition_run(
            run=run,
            target=CampaignRunStatus.AWAITING_HUMAN_DECISION,
            campaign_id=campaign.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.events.record(
            workspace_id=run.workspace_id,
            event_type=EVENT_DECISION_OPENED,
            actor_type=ActorType.USER,
            campaign_id=campaign.id,
            campaign_run_id=run.id,
            stage_execution_id=stage_execution_id,
            decision_request_id=request.id,
            new_state=DecisionRequestStatus.OPEN.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return request

    def respond_to_decision(
        self,
        *,
        campaign: Campaign,
        run: CampaignRun,
        decision_public_id: str,
        responder_user_id: uuid.UUID,
        response_text: str,
        request_id: str | None,
    ) -> HumanDecisionResponse:
        # Row-locked so two concurrent responses to the same request
        # cannot both observe OPEN and both proceed (BACKEND-06 §29).
        request = self.decision_requests.get_by_public_id(decision_public_id, for_update=True)
        if request is None or request.campaign_run_id != run.id:
            raise ForbiddenError()
        if request.status is not DecisionRequestStatus.OPEN:
            raise DecisionAlreadyResolvedError()

        response = self.decision_responses.create(
            decision_request=request, responded_by_user_id=responder_user_id, response_text=response_text
        )
        request.status = DecisionRequestStatus.RESOLVED
        request.resolved_at = datetime.now(timezone.utc)
        self.events.record(
            workspace_id=run.workspace_id,
            event_type=EVENT_DECISION_RESOLVED,
            actor_type=ActorType.USER,
            campaign_id=campaign.id,
            campaign_run_id=run.id,
            stage_execution_id=request.stage_execution_id,
            decision_request_id=request.id,
            previous_state=DecisionRequestStatus.OPEN.value,
            new_state=DecisionRequestStatus.RESOLVED.value,
            actor_user_id=responder_user_id,
            request_id=request_id,
        )

        # BACKEND-01 §4: "resume() is only callable with a RESOLVED
        # request as input" — resolving the one open decision that
        # paused this run is the entirety of "resume" BACKEND-06
        # implements; there is no Handoff to advance yet.
        if run.status is CampaignRunStatus.AWAITING_HUMAN_DECISION:
            self._transition_run(
                run=run,
                target=CampaignRunStatus.RUNNING,
                campaign_id=campaign.id,
                actor_user_id=responder_user_id,
                request_id=request_id,
            )

        self.session.commit()
        return response
