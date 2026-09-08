"""Strategy persistence — BACKEND-08.

No public HTTP write endpoint exists (§19) — BACKEND-01's own API map
defines `/campaigns/{id}/strategy` as GET-only. This service is the
controlled, service-layer-only mechanism a future runtime (once real Agent
Run/Gate Decision exist) will call to persist output; tests call it
directly today, the same shape as ``ResearchService``/
``OrchestrationService.create_decision_request``.

Two distinct transaction boundaries, not one, because Strategy/Positioning/
Hypothesis/Experiment have genuinely different lifecycles:

1. ``record_strategy`` — the atomic *initial* write: Strategy + Positioning
   + any initial Hypotheses (+ their initial Experiments) + one AuditEvent
   per created entity, all in one transaction. If anything raises before
   the final commit, nothing persists.
2. ``transition_hypothesis`` — a later, independent status change on one
   existing Hypothesis: the mutation + its own AuditEvent, in their own
   separate transaction.

PERSISTING STRATEGY OUTPUT NEVER MUTATES CampaignRun.status,
RunStageExecution.status, or any HumanDecisionRequest/Response (§21/§Q) —
no code below touches any of those tables. Nothing here creates,
references, or implies a Strategic Decision, a Gate Decision, a Strategy
Approval, or a Learning Candidate (§20) — those entities are not
implemented in this codebase.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign, CampaignRun
from app.core.api_errors import ForbiddenError, InvalidLifecycleTransitionError, ProvenanceMismatchError, VersionConflictError
from app.orchestration.models import BusinessStage, RunStageExecution
from app.strategy.models import Experiment, Hypothesis, HypothesisStatus, Positioning, Strategy
from app.strategy.repository import (
    ExperimentRepository,
    HypothesisRepository,
    PositioningRepository,
    StrategyRepository,
)
from app.strategy.transitions import is_legal_hypothesis_transition

EVENT_STRATEGY_RECORDED = "strategy.recorded"
EVENT_HYPOTHESIS_RECORDED = "strategy.hypothesis.recorded"
EVENT_HYPOTHESIS_STATUS_CHANGED = "strategy.hypothesis.status_changed"
EVENT_EXPERIMENT_RECORDED = "strategy.experiment.recorded"


def _validate_provenance(
    *, campaign: Campaign, campaign_run: CampaignRun, stage_execution: RunStageExecution, expected_stage: BusinessStage
) -> None:
    """Mirrors ``app/research/service.py::_validate_provenance`` exactly —
    a plain/composite FK alone cannot prove this; each check is explicit
    here. Any failure raises the same non-leaky ``ProvenanceMismatchError``."""
    if campaign_run.campaign_id != campaign.id:
        raise ProvenanceMismatchError()
    if campaign_run.workspace_id != campaign.workspace_id:
        raise ProvenanceMismatchError()
    if stage_execution.campaign_run_id != campaign_run.id:
        raise ProvenanceMismatchError()
    if stage_execution.stage is not expected_stage:
        raise ProvenanceMismatchError()


class StrategyService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.strategies = StrategyRepository(session)
        self.positionings = PositioningRepository(session)
        self.hypotheses = HypothesisRepository(session)
        self.experiments = ExperimentRepository(session)
        self.events = AuditEventRepository(session)

    # --- reads (GET-only public surface calls this) -----------------

    def get_strategy_output(
        self, *, campaign: Campaign
    ) -> tuple[Strategy | None, Positioning | None, list[Hypothesis], list[Experiment]]:
        strategy = self.strategies.get_current_for_campaign(campaign.id)
        if strategy is None:
            return None, None, [], []
        positioning = self.positionings.get_for_strategy(strategy.id)
        hypotheses = self.hypotheses.list_for_strategy(strategy.id)
        experiments: list[Experiment] = []
        for hypothesis in hypotheses:
            experiments.extend(self.experiments.list_for_hypothesis(hypothesis.id))
        return strategy, positioning, hypotheses, experiments

    # --- writes (service-layer only; no public HTTP trigger) ---------

    def record_strategy(
        self,
        *,
        campaign: Campaign,
        campaign_run: CampaignRun,
        stage_execution: RunStageExecution,
        summary: str,
        positioning_statement: str,
        hypotheses: list[dict] | None = None,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> tuple[Strategy, Positioning, list[Hypothesis], list[Experiment]]:
        """Atomically creates Strategy + Positioning + any initial
        Hypotheses (+ their initial Experiments) + one AuditEvent per
        created entity. ``hypotheses`` is a list of
        ``{"statement": str, "experiments": [{"description": str}, ...]}``
        dicts (``experiments`` optional/empty per hypothesis)."""
        _validate_provenance(
            campaign=campaign, campaign_run=campaign_run, stage_execution=stage_execution,
            expected_stage=BusinessStage.STRATEGY,
        )
        hypotheses = hypotheses or []
        next_version = self.strategies.next_version_for_campaign(campaign.id)

        # Only the parent-row insert is version-conflict-sensitive — the
        # DB's (campaign_id, version) unique constraint is authoritative
        # (mirrors app/research/service.py's own narrowed try/except).
        try:
            strategy = self.strategies.create(
                campaign=campaign, campaign_run=campaign_run, stage_execution=stage_execution,
                version=next_version, summary=summary,
            )
        except IntegrityError:
            self.session.rollback()
            raise VersionConflictError() from None

        # Nothing below calls commit() until every child row and every
        # AuditEvent has succeeded — if anything raises, the caller's own
        # session-lifecycle wrapper discards the parent row too, since it
        # was only flushed, never committed.
        positioning = self.positionings.create(strategy=strategy, statement=positioning_statement)

        created_hypotheses = self.hypotheses.create_many(strategy=strategy, items=hypotheses) if hypotheses else []
        created_experiments: list[Experiment] = []
        for hypothesis_input, hypothesis_row in zip(hypotheses, created_hypotheses):
            experiment_items = hypothesis_input.get("experiments") or []
            if experiment_items:
                created_experiments.extend(
                    self.experiments.create_many(hypothesis=hypothesis_row, items=experiment_items)
                )

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_STRATEGY_RECORDED,
            actor_type=actor_type,
            campaign_id=campaign.id,
            campaign_run_id=campaign_run.id,
            stage_execution_id=stage_execution.id,
            strategy_id=strategy.id,
            new_state=f"v{strategy.version}",
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        for hypothesis_row in created_hypotheses:
            self.events.record(
                workspace_id=campaign.workspace_id,
                event_type=EVENT_HYPOTHESIS_RECORDED,
                actor_type=actor_type,
                campaign_id=campaign.id,
                strategy_id=strategy.id,
                hypothesis_id=hypothesis_row.id,
                new_state=HypothesisStatus.OPEN.value,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
        for experiment_row in created_experiments:
            self.events.record(
                workspace_id=campaign.workspace_id,
                event_type=EVENT_EXPERIMENT_RECORDED,
                actor_type=actor_type,
                campaign_id=campaign.id,
                strategy_id=strategy.id,
                hypothesis_id=experiment_row.hypothesis_id,
                experiment_id=experiment_row.id,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )

        self.session.commit()
        return strategy, positioning, created_hypotheses, created_experiments

    def transition_hypothesis(
        self,
        *,
        campaign: Campaign,
        hypothesis_public_id: str,
        target_status: HypothesisStatus,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> Hypothesis:
        """Independent transaction: the status mutation + its own
        AuditEvent. Row-locked (mirrors
        ``OrchestrationService.respond_to_decision``) so two concurrent
        transition attempts on the same Hypothesis cannot both observe the
        pre-transition status and both proceed."""
        hypothesis = self.hypotheses.get_by_public_id(hypothesis_public_id, for_update=True)
        # Non-leaky: a hypothesis that does not exist, or that exists but
        # belongs to a different workspace, is indistinguishable (mirrors
        # CampaignAccessService's own precedent).
        if hypothesis is None or hypothesis.workspace_id != campaign.workspace_id:
            raise ForbiddenError()
        if not is_legal_hypothesis_transition(hypothesis.status, target_status):
            raise InvalidLifecycleTransitionError(
                f"Cannot transition a hypothesis from {hypothesis.status.value} to {target_status.value}."
            )

        previous = hypothesis.status
        hypothesis.status = target_status
        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_HYPOTHESIS_STATUS_CHANGED,
            actor_type=ActorType.USER if actor_user_id is not None else ActorType.SYSTEM,
            campaign_id=campaign.id,
            strategy_id=hypothesis.strategy_id,
            hypothesis_id=hypothesis.id,
            previous_state=previous.value,
            new_state=target_status.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return hypothesis
