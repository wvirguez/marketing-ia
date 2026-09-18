"""Planning persistence — BACKEND-09, extended by MVP-33B (frozen
MVP-33A/-33A-R1 contract).

``record_plan`` remains the deterministic-bootstrap-only write
(``origin=BOOTSTRAP``) — no HTTP route ever calls it directly, only
``app/orchestration/service.py``'s own bootstrap flow. ``create_plan`` is
the new governed, human-reachable counterpart (``origin=GOVERNED``),
exposed via ``POST /campaigns/{id}/plan`` (``app/planning/router.py``).
Both share the same ``ContentPlanRepository``/``PlanItemRepository`` and
the same ``EVENT_PLAN_RECORDED``/``EVENT_PLAN_ITEM_RECORDED`` audit
namespace — no split.

Two independent transaction boundaries, one per write method — BACKEND-09
has no mutation operation analogous to ``transition_hypothesis``; Plan Item
mutability is canonically ACKNOWLEDGED but NOT IMPLEMENTED (see
``app/planning/models.py``).

Each write method is the atomic *initial* write for its own ContentPlan
version: Content Plan + any initial Plan Items + one AuditEvent per created
entity, all in one transaction. If anything raises before the final
commit, nothing persists.

PERSISTING PLAN OUTPUT NEVER MUTATES CampaignRun.status,
RunStageExecution.status, or any HumanDecisionRequest/Response — no code
below touches any of those tables. Nothing here creates, references, or
implies a Content Brief, a Content Piece, a Strategic Decision, a Gate
Decision, a Plan Approval, or Production Authorization — those entities are
not implemented in this codebase or are out of scope for this bounded
context. ``create_plan`` reads (never mutates) at most one Experiment.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign, CampaignRun
from app.core.api_errors import ForbiddenError, ProvenanceMismatchError, VersionConflictError
from app.orchestration.models import BusinessStage, RunStageExecution
from app.planning.models import ContentPlan, ContentPlanOrigin, PlanItem
from app.planning.repository import ContentPlanRepository, PlanItemRepository
from app.strategy.repository import ExperimentRepository

EVENT_PLAN_RECORDED = "planning.plan.recorded"
EVENT_PLAN_ITEM_RECORDED = "planning.plan_item.recorded"


def _validate_provenance(
    *, campaign: Campaign, campaign_run: CampaignRun, stage_execution: RunStageExecution, expected_stage: BusinessStage
) -> None:
    """Mirrors ``app/strategy/service.py::_validate_provenance`` exactly —
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


class PlanningService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.plans = ContentPlanRepository(session)
        self.items = PlanItemRepository(session)
        self.events = AuditEventRepository(session)
        self.experiments = ExperimentRepository(session)

    # --- reads (GET-only public surface calls this) -----------------

    def get_plan_output(self, *, campaign: Campaign) -> tuple[ContentPlan | None, list[PlanItem]]:
        plan = self.plans.get_current_for_campaign(campaign.id)
        if plan is None:
            return None, []
        items = self.items.list_for_plan(plan.id)
        return plan, items

    # --- writes (service-layer only; no public HTTP trigger) ---------

    def record_plan(
        self,
        *,
        campaign: Campaign,
        campaign_run: CampaignRun,
        stage_execution: RunStageExecution,
        summary: str,
        items: list[dict] | None = None,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> tuple[ContentPlan, list[PlanItem]]:
        """Atomically creates Content Plan + any initial Plan Items + one
        AuditEvent per created entity. ``items`` is a list of
        ``{"format": str, "objective": str, "sequence": int,
        "scheduled_date": date | None}`` dicts."""
        _validate_provenance(
            campaign=campaign, campaign_run=campaign_run, stage_execution=stage_execution,
            expected_stage=BusinessStage.PLAN,
        )
        items = items or []
        next_version = self.plans.next_version_for_campaign(campaign.id)

        # Only the parent-row insert is version-conflict-sensitive — the
        # DB's (campaign_id, version) unique constraint is authoritative
        # (mirrors app/strategy/service.py's own narrowed try/except).
        try:
            plan = self.plans.create(
                campaign=campaign, campaign_run=campaign_run, stage_execution=stage_execution,
                origin=ContentPlanOrigin.BOOTSTRAP, version=next_version, summary=summary,
            )
        except IntegrityError:
            self.session.rollback()
            raise VersionConflictError() from None

        # Nothing below calls commit() until every child row and every
        # AuditEvent has succeeded — if anything raises, the caller's own
        # session-lifecycle wrapper discards the parent row too, since it
        # was only flushed, never committed.
        created_items = self.items.create_many(plan=plan, items=items) if items else []

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_PLAN_RECORDED,
            actor_type=actor_type,
            campaign_id=campaign.id,
            campaign_run_id=campaign_run.id,
            stage_execution_id=stage_execution.id,
            content_plan_id=plan.id,
            new_state=f"v{plan.version}",
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        for item_row in created_items:
            self.events.record(
                workspace_id=campaign.workspace_id,
                event_type=EVENT_PLAN_ITEM_RECORDED,
                actor_type=actor_type,
                campaign_id=campaign.id,
                content_plan_id=plan.id,
                plan_item_id=item_row.id,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )

        self.session.commit()
        return plan, created_items

    def create_plan(
        self,
        *,
        campaign: Campaign,
        summary: str,
        experiment_public_id: str | None,
        items: list[dict] | None = None,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> tuple[ContentPlan, list[PlanItem]]:
        """MVP-33A/-33A-R1 (frozen contract, implemented MVP-33B): the
        governed, human-reachable counterpart to ``record_plan``'s own
        bootstrap-bound creation. A human may propose a new ContentPlan for
        a Campaign, optionally naming one Experiment it operationalizes
        (Case E) or naming none (Case G, generic). ``origin=GOVERNED`` —
        ``campaign_run``/``stage_execution`` are never supplied, because no
        human-reachable, not-yet-consumed CampaignRun/PLAN-StageExecution
        instance can exist (MVP-33A-R1 §F: the bootstrap consumes the only
        one synchronously inside ``start_run``). If ``experiment_public_id``
        is given, the Experiment is resolved through a campaign-scoped
        repository JOIN (``ExperimentRepository.get_for_campaign_by_public_id``)
        — same-Workspace is DB-enforced by the composite FK on ContentPlan
        itself, same-Campaign is proven by this JOIN, never assumed
        (MVP-33A-R1 §D/§E). No Strategy-currency recheck, no lock — a
        historical-Strategy Experiment remains fully eligible (MVP-33A §N:
        E2 ALLOWED); an unresolvable Experiment (wrong Campaign, wrong
        Workspace, or simply unknown) is a non-leaky ``ForbiddenError``,
        never distinguished from "does not exist"."""
        experiment = None
        if experiment_public_id is not None:
            experiment = self.experiments.get_for_campaign_by_public_id(
                campaign_id=campaign.id, public_id=experiment_public_id
            )
            if experiment is None:
                raise ForbiddenError()

        items = items or []
        next_version = self.plans.next_version_for_campaign(campaign.id)

        try:
            plan = self.plans.create(
                campaign=campaign, origin=ContentPlanOrigin.GOVERNED,
                version=next_version, summary=summary, experiment=experiment,
            )
        except IntegrityError:
            self.session.rollback()
            raise VersionConflictError() from None

        created_items = self.items.create_many(plan=plan, items=items) if items else []

        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_PLAN_RECORDED,
            actor_type=ActorType.USER,
            campaign_id=campaign.id,
            content_plan_id=plan.id,
            experiment_id=experiment.id if experiment is not None else None,
            new_state=f"v{plan.version}",
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        for item_row in created_items:
            self.events.record(
                workspace_id=campaign.workspace_id,
                event_type=EVENT_PLAN_ITEM_RECORDED,
                actor_type=ActorType.USER,
                campaign_id=campaign.id,
                content_plan_id=plan.id,
                plan_item_id=item_row.id,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )

        self.session.commit()
        return plan, created_items
