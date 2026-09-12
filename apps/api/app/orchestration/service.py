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
from app.campaigns.repository import CampaignBriefRepository
from app.content.service import ContentService
from app.core.api_errors import (
    DecisionAlreadyResolvedError,
    ForbiddenError,
    InvalidLifecycleTransitionError,
    OrchestrationNotInitializedError,
)
from app.orchestration import bootstrap as bootstrap_content
from app.orchestration.models import (
    BusinessStage,
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
from app.planning.service import PlanningService
from app.research.service import ResearchService
from app.strategy.service import StrategyService

EVENT_ORCHESTRATION_INITIALIZED = "orchestration.initialized"
EVENT_RUN_TRANSITIONED = "orchestration.run.transitioned"
EVENT_STAGE_TRANSITIONED = "orchestration.stage.transitioned"
EVENT_DECISION_OPENED = "orchestration.decision.opened"
EVENT_DECISION_RESOLVED = "orchestration.decision.resolved"
# MVP-06E: orchestration-level bootstrap lifecycle envelope events — purely
# observational (never a state-machine input), attributed the same way as
# every other orchestration-level lifecycle event above (ActorType.USER,
# the real actor_user_id) rather than SYSTEM, since "the user's request
# caused this bootstrap to run" is exactly as true here as it already is
# for EVENT_RUN_TRANSITIONED/EVENT_STAGE_TRANSITIONED — only the domain
# content _produced_ by the bootstrap is SYSTEM-attributed.
EVENT_BOOTSTRAP_STARTED = "orchestration.bootstrap.started"
EVENT_BOOTSTRAP_COMPLETED = "orchestration.bootstrap.completed"
EVENT_BOOTSTRAP_FAILED = "orchestration.bootstrap.failed"

_FIRST_STAGE_ORDINAL = 1

# MVP-04/MVP-05E: the only business stages a deterministic bootstrap is
# authorized to populate. CREATIVE and every later stage are explicitly out
# of scope and must remain PENDING — see docs/backend (MVP-04 Phase 1 gate)
# and the MVP-05D/05E architecture reviews for CONTENT specifically.
_BOOTSTRAP_STAGE_ORDER: tuple[BusinessStage, ...] = (
    BusinessStage.RESEARCH,
    BusinessStage.AUDIENCE,
    BusinessStage.STRATEGY,
    BusinessStage.PLAN,
    BusinessStage.CONTENT,
)


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

    # --- MVP-04: deterministic content bootstrap --------------------------

    def run_deterministic_bootstrap(
        self, *, campaign: Campaign, run: CampaignRun, actor_user_id: uuid.UUID | None, request_id: str | None
    ) -> None:
        """Populates RESEARCH -> AUDIENCE -> STRATEGY -> PLAN -> CONTENT
        with a deterministic, Campaign-Brief-derived synthesis, immediately
        after ``start_run`` promotes stage #1 to READY. CREATIVE and every
        later business stage are explicitly out of scope and are never
        touched — this method never promotes CREATIVE past PENDING.

        SYSTEM activity only: every domain write below passes
        ``actor_user_id=None`` unconditionally, regardless of the real
        ``actor_user_id`` this method itself received, so each domain
        service resolves ``ActorType.SYSTEM`` (never ``AGENT`` — no
        specialist agent exists or ran; never ``USER`` — the authenticated
        user did not author this content). The orchestration-level stage
        transitions this method performs (via ``_transition_stage``) keep
        their existing, unmodified ``ActorType.USER`` attribution and the
        real ``actor_user_id`` — unchanged BACKEND-06 behavior, since "the
        user's request caused this stage to move" remains literally true;
        only "who produced this content" is SYSTEM.

        Never creates a Handoff/Return/Gate Decision/Agent Run — those
        tables do not exist in this codebase and none is added here.
        """
        brief = CampaignBriefRepository(self.session).get_latest_for_campaign(campaign.id)
        if brief is None:  # pragma: no cover - create_campaign always creates one atomically
            raise RuntimeError("Campaign has no CampaignBrief; cannot run the deterministic bootstrap.")

        stages_by_stage = {s.stage: s for s in self.stages.list_for_run(campaign_run_id=run.id)}
        stage_sequence = [stages_by_stage[stage] for stage in _BOOTSTRAP_STAGE_ORDER]

        research_service = ResearchService(self.session)
        strategy_service = StrategyService(self.session)
        planning_service = PlanningService(self.session)
        content_service = ContentService(self.session)

        research_stage, audience_stage, strategy_stage, plan_stage, content_stage = stage_sequence

        # MVP-06E: durable envelope-start marker, committed on its own
        # before any stage runs — if RESEARCH or any later stage fails,
        # this record must still exist (BACKEND-06 §28-style atomicity
        # applies to it in the other direction: nothing that happens
        # afterward can ever roll it back, since it is already committed).
        self.events.record(
            workspace_id=run.workspace_id,
            event_type=EVENT_BOOTSTRAP_STARTED,
            actor_type=ActorType.USER,
            campaign_id=campaign.id,
            campaign_run_id=run.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()

        self._run_bootstrap_stage(
            campaign=campaign,
            stage_execution=research_stage,
            next_stage_execution=audience_stage,
            actor_user_id=actor_user_id,
            request_id=request_id,
            domain_writer=lambda: research_service.record_report(
                campaign=campaign,
                campaign_run=run,
                stage_execution=research_stage,
                summary=bootstrap_content.build_research_summary(campaign_name=campaign.name, brief=brief),
                sources=[],
            ),
        )

        self._run_bootstrap_stage(
            campaign=campaign,
            stage_execution=audience_stage,
            next_stage_execution=strategy_stage,
            actor_user_id=actor_user_id,
            request_id=request_id,
            domain_writer=lambda: research_service.record_audience_profile(
                campaign=campaign,
                campaign_run=run,
                stage_execution=audience_stage,
                summary=bootstrap_content.build_audience_summary(campaign_name=campaign.name, brief=brief),
                voc_items=[],
            ),
        )

        strategy_content = bootstrap_content.build_strategy_content(campaign_name=campaign.name, brief=brief)
        self._run_bootstrap_stage(
            campaign=campaign,
            stage_execution=strategy_stage,
            next_stage_execution=plan_stage,
            actor_user_id=actor_user_id,
            request_id=request_id,
            domain_writer=lambda: strategy_service.record_strategy(
                campaign=campaign,
                campaign_run=run,
                stage_execution=strategy_stage,
                summary=strategy_content["summary"],
                positioning_statement=strategy_content["positioning_statement"],
                hypotheses=strategy_content["hypotheses"],
            ),
        )

        def _write_plan():
            # MVP-04R: Planning must consume the actual persisted Strategy
            # produced by the STRATEGY stage above — re-read it from
            # persistence (the same read StrategyService's own GET-only
            # public surface uses) rather than assuming an in-memory value
            # survived from the STRATEGY step. If no persisted Strategy/
            # Positioning exists for this Campaign, Planning must NOT fall
            # back to synthesizing from CampaignBrief alone: this stage
            # writer raises, so the existing failure handling in
            # ``_run_bootstrap_stage`` marks PLAN FAILED and creates no
            # ContentPlan (§8/§10 of the MVP-04R repair).
            strategy, positioning, _hypotheses, _experiments = strategy_service.get_strategy_output(
                campaign=campaign
            )
            if strategy is None or positioning is None:
                raise RuntimeError(
                    "Campaign has no persisted Strategy/Positioning; Planning cannot "
                    "bootstrap without the STRATEGY stage's actual output."
                )
            plan_content = bootstrap_content.build_plan_content(
                campaign_name=campaign.name, brief=brief, strategy_positioning=positioning
            )
            return planning_service.record_plan(
                campaign=campaign,
                campaign_run=run,
                stage_execution=plan_stage,
                summary=plan_content["summary"],
                items=plan_content["items"],
            )

        self._run_bootstrap_stage(
            campaign=campaign,
            stage_execution=plan_stage,
            # MVP-05E: PLAN COMPLETED now promotes CONTENT PENDING -> READY.
            next_stage_execution=content_stage,
            actor_user_id=actor_user_id,
            request_id=request_id,
            domain_writer=_write_plan,
        )

        def _write_content():
            # MVP-05E: Content must derive from PERSISTED upstream state,
            # never the in-memory `strategy_content`/`plan_content` dicts
            # built above — re-read the real persisted ContentPlan/PlanItem
            # rows and the real persisted Positioning, the same "re-read
            # from persistence" discipline `_write_plan` already applies to
            # Strategy above (MVP-04R).
            content_plan, plan_items = planning_service.get_plan_output(campaign=campaign)
            if content_plan is None:
                raise RuntimeError(
                    "Campaign has no persisted Content Plan; Content cannot "
                    "bootstrap without the PLAN stage's actual output."
                )
            _strategy, positioning, _hypotheses, _experiments = strategy_service.get_strategy_output(
                campaign=campaign
            )
            if positioning is None:
                raise RuntimeError(
                    "Campaign has no persisted Strategy/Positioning; Content "
                    "cannot bootstrap without the STRATEGY stage's actual "
                    "output."
                )

            # Zero PlanItems is a legitimate, already-supported ContentPlan
            # state (PlanningService.record_plan accepts an empty/None
            # `items` list) — with nothing to materialize, this loop is
            # vacuously complete rather than an error (MVP-05E §20 design
            # decision).
            for plan_item in plan_items:
                # Idempotency (defense-in-depth, MVP-05E §22/§23/§24): a
                # Brief already exists for this PlanItem (its own DB
                # UniqueConstraint on plan_item_id is the authoritative
                # guard) or a Piece already exists for that Brief (no
                # DB-level uniqueness exists for content_brief_id, so this
                # check is the actual guard) means this PlanItem was
                # already materialized by an earlier attempt — never
                # create a second Brief, Piece, or Version for it.
                content_brief = content_service.get_brief_for_plan_item(plan_item.id)
                if content_brief is None:
                    brief_text = bootstrap_content.build_content_brief(
                        campaign_name=campaign.name,
                        brief=brief,
                        strategy_positioning=positioning,
                        plan_item=plan_item,
                    )
                    content_brief = content_service.record_brief(
                        plan_item=plan_item, content_plan=content_plan, brief=brief_text
                    )

                existing_piece = content_service.get_piece_for_brief(content_brief.id)
                if existing_piece is not None:
                    # Consistency check (MVP-05E §26): record_piece creates
                    # Piece + initial Version atomically, so a Piece found
                    # here must already have a Version. If it does not,
                    # persisted state is inconsistent in a way this
                    # deterministic bootstrap never itself produces — fail
                    # safe rather than guessing a repair.
                    if content_service.get_latest_version_for_piece(existing_piece.id) is None:
                        raise RuntimeError(
                            "Inconsistent Content state: a Content Piece exists "
                            "without any Content Version; refusing to repair "
                            "automatically."
                        )
                    continue

                piece_fields = bootstrap_content.build_content_piece_fields(plan_item=plan_item, brief=brief)
                version_payload = bootstrap_content.build_content_version_payload(
                    plan_item=plan_item, strategy_positioning=positioning
                )
                # MVP-05E §13: leaves ContentPiece.status at its DRAFT
                # creation default — record_piece never advances status, and
                # no production-transition or approval-recording method of
                # any kind is ever invoked anywhere in this method (see
                # ``test_bootstrap_code_never_references_content_approval_
                # tracking_measurement_or_learning_write_methods``) — zero
                # ContentApproval rows are ever created by this bootstrap.
                content_service.record_piece(
                    content_brief=content_brief,
                    initial_payload=version_payload,
                    **piece_fields,
                )

        self._run_bootstrap_stage(
            campaign=campaign,
            stage_execution=content_stage,
            # No next stage: CREATIVE is explicitly out of MVP-05E's scope
            # and must remain PENDING.
            next_stage_execution=None,
            actor_user_id=actor_user_id,
            request_id=request_id,
            domain_writer=_write_content,
        )

    def _run_bootstrap_stage(
        self,
        *,
        campaign: Campaign,
        stage_execution: RunStageExecution,
        next_stage_execution: RunStageExecution | None,
        actor_user_id: uuid.UUID | None,
        request_id: str | None,
        domain_writer,
    ) -> None:
        """One committed unit per domain/stage (never one giant bootstrap
        transaction): stage -> RUNNING is committed on its own, the domain
        write commits itself (each ``record_*`` method already owns its
        own transaction), then stage -> COMPLETED + the next authorized
        stage -> READY commit together. A failure at any point rolls back
        only the in-flight attempt, marks the failing stage FAILED in its
        own commit, and re-raises — prior, already-committed stages are
        never touched. Idempotency guard: a stage already COMPLETED is
        never re-run."""
        if stage_execution.status is StageExecutionStatus.COMPLETED:
            return

        try:
            if stage_execution.status is StageExecutionStatus.PENDING:
                self._transition_stage(
                    stage_execution=stage_execution,
                    target=StageExecutionStatus.READY,
                    campaign_id=campaign.id,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )
                self.session.commit()

            if stage_execution.status is StageExecutionStatus.READY:
                self._transition_stage(
                    stage_execution=stage_execution,
                    target=StageExecutionStatus.RUNNING,
                    campaign_id=campaign.id,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )
                self.session.commit()

            domain_writer()

            self._transition_stage(
                stage_execution=stage_execution,
                target=StageExecutionStatus.COMPLETED,
                campaign_id=campaign.id,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
            if next_stage_execution is not None:
                self._transition_stage(
                    stage_execution=next_stage_execution,
                    target=StageExecutionStatus.READY,
                    campaign_id=campaign.id,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )
            if stage_execution.stage is BusinessStage.CONTENT:
                # MVP-06E-R1: bootstrap.completed must be atomic with
                # CONTENT's own COMPLETED transition — recorded in this
                # same, still-uncommitted transaction so the single
                # `session.commit()` below either persists both together
                # or neither. Never a second, later transaction: if this
                # write itself raises, execution falls straight into the
                # `except` block below, which rolls back this entire
                # attempt (including the COMPLETED transition just above)
                # exactly like any other in-flight bootstrap failure —
                # there is no special-case that lets CONTENT stay
                # COMPLETED without a corresponding bootstrap.completed.
                # Explicitly tied to BusinessStage.CONTENT (never inferred
                # merely from `next_stage_execution is None`) so completion
                # semantics stay anchored to the real business boundary.
                self.events.record(
                    workspace_id=stage_execution.workspace_id,
                    event_type=EVENT_BOOTSTRAP_COMPLETED,
                    actor_type=ActorType.USER,
                    campaign_id=campaign.id,
                    campaign_run_id=stage_execution.campaign_run_id,
                    stage_execution_id=stage_execution.id,
                    new_state=BusinessStage.CONTENT.value,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            self.session.refresh(stage_execution)
            if stage_execution.status is StageExecutionStatus.RUNNING:
                self._transition_stage(
                    stage_execution=stage_execution,
                    target=StageExecutionStatus.FAILED,
                    campaign_id=campaign.id,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )
            # MVP-06E: the bootstrap-envelope failure event is recorded
            # unconditionally here — never gated on `stage_execution.status
            # is RUNNING` the way the stage's own FAILED transition above
            # is. That condition only governs whether *this specific
            # stage* legally transitions to FAILED (it may still be
            # PENDING/READY if the exception struck before RUNNING was
            # ever durably committed); every exception that reaches this
            # except block is still a real bootstrap failure and must
            # still produce exactly one `bootstrap.failed`, attributing it
            # to whichever stage was in flight when it happened. Recorded
            # after `rollback()`/`refresh()` (so it cannot be erased by
            # the same rollback that caused it) and committed together
            # with the conditional FAILED transition above when that
            # transition also applies.
            self.events.record(
                workspace_id=stage_execution.workspace_id,
                event_type=EVENT_BOOTSTRAP_FAILED,
                actor_type=ActorType.USER,
                campaign_id=campaign.id,
                campaign_run_id=stage_execution.campaign_run_id,
                stage_execution_id=stage_execution.id,
                new_state=stage_execution.stage.value,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
            self.session.commit()
            raise

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
