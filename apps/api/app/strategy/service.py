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
from app.core.api_errors import (
    ExperimentStrategyStaleError,
    ForbiddenError,
    HypothesisStrategyStaleError,
    InvalidLifecycleTransitionError,
    ProvenanceMismatchError,
    VersionConflictError,
)
from app.orchestration.models import BusinessStage, RunStageExecution
from app.strategy.models import Experiment, Hypothesis, HypothesisStatus, Positioning, Strategy, StrategyOrigin
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

    def list_versions_for_campaign(self, campaign_id: uuid.UUID) -> list[Strategy]:
        """MVP-30B: every Strategy version for a Campaign, oldest first —
        the read side of the governed-history surface
        (``app/orchestration/strategy_revision_router.py``)."""
        return self.strategies.list_for_campaign(campaign_id)

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
                version=next_version, summary=summary, origin=StrategyOrigin.BOOTSTRAP,
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

    def create_hypothesis(
        self,
        *,
        campaign: Campaign,
        strategy_public_id: str,
        statement: str,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> Hypothesis:
        """MVP-31A/-31A-R1 (frozen contract, implemented MVP-31B): the
        governed, human-reachable counterpart to ``record_strategy``'s own
        bootstrap-bound Hypothesis creation. A human may propose a new
        Hypothesis under the Campaign's own current Strategy — of either
        ``origin`` (MVP-31A §H: BOOTSTRAP and REVISION are both eligible,
        never restricted to REVISION only) — naming it explicitly by its
        own ``public_id`` (never resolved as "whatever is current",
        MVP-31A §31) and never silently rebased if it has since gone stale
        (``HypothesisStrategyStaleError``). Canonical lock = the exact
        Strategy row only (MVP-31A §28/§29) — this method never locks or
        even reads a StrategicDecision/StrategicApproval/StrategyRevision
        row, so it cannot participate in any lock-order cycle with
        ``StrategyRevisionService.revise_strategy``. No ``origin``
        discriminator exists on ``Hypothesis`` (MVP-31A-R1) — the created
        row is indistinguishable, by any stored column, from one produced
        by the bootstrap path."""
        strategy = self.strategies.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=strategy_public_id, for_update=True
        )
        if strategy is None:
            raise ForbiddenError()
        current_strategy = self.strategies.get_current_for_campaign(campaign.id)
        if current_strategy is None or current_strategy.id != strategy.id:
            raise HypothesisStrategyStaleError()

        hypothesis = self.hypotheses.create(strategy=strategy, statement=statement)

        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_HYPOTHESIS_RECORDED,
            actor_type=ActorType.USER,
            campaign_id=campaign.id,
            strategy_id=strategy.id,
            hypothesis_id=hypothesis.id,
            new_state=HypothesisStatus.OPEN.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return hypothesis

    def create_experiment(
        self,
        *,
        campaign: Campaign,
        hypothesis_public_id: str,
        description: str,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> Experiment:
        """MVP-32A/-32A-R1 (frozen contract, implemented MVP-32B): the
        governed, human-reachable counterpart to ``record_strategy``'s own
        bootstrap-bound Experiment creation (which production never
        actually exercises — ``app/orchestration/bootstrap.py`` always
        synthesizes zero experiments). A human may propose a new
        Experiment under any Hypothesis (any status — OPEN, CONFIRMED, or
        REFUTED are all eligible, MVP-32A §L) PROVIDED that Hypothesis's
        own parent Strategy is still the Campaign's current version at the
        moment that Strategy row is locked (MVP-32A-R1 §14: Option A —
        historical-Strategy Hypotheses are NOT eligible for new Experiment
        creation, never silently rebased). Strategy identity is derived
        exclusively from the persisted ``Hypothesis.strategy_id`` FK —
        never client-supplied, so no Strategy appears in the route/payload
        (MVP-32A §21/MVP-32A-R1: no ambiguity for a client to get wrong,
        since they never choose a Strategy at all). Canonical lock = the
        exact Strategy row referenced by the Hypothesis (MVP-32A-R1 §9) —
        this method never locks or reads a StrategicDecision/
        StrategicApproval/StrategyRevision row, so it cannot participate
        in any lock-order cycle with ``StrategyRevisionService.
        revise_strategy`` or ``StrategyService.create_hypothesis`` (same
        single-resource acyclicity proof, one level down). No
        ``ExperimentStatus`` enum exists (MVP-32A-R1 §10-13: a
        single-value vocabulary does not justify a migration) —
        ``status`` is the literal string ``"RECORDED"``, written via
        ``ExperimentRepository.create``."""
        hypothesis = self.hypotheses.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=hypothesis_public_id
        )
        if hypothesis is None:
            raise ForbiddenError()

        strategy = self.strategies.get_by_id(hypothesis.strategy_id, for_update=True)
        if strategy is None:  # pragma: no cover - FK guarantees existence
            raise ForbiddenError()
        current_strategy = self.strategies.get_current_for_campaign(campaign.id)
        if current_strategy is None or current_strategy.id != strategy.id:
            raise ExperimentStrategyStaleError()

        experiment = self.experiments.create(hypothesis=hypothesis, description=description)

        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_EXPERIMENT_RECORDED,
            actor_type=ActorType.USER,
            campaign_id=campaign.id,
            strategy_id=strategy.id,
            hypothesis_id=hypothesis.id,
            experiment_id=experiment.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return experiment

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
