"""Planning persistence — BACKEND-09.

No public HTTP write endpoint exists (§18) — BACKEND-01's own API map
defines `/campaigns/{id}/plan` as GET-only. This service is the controlled,
service-layer-only mechanism a future runtime (once real Agent Run/Gate
Decision exist) will call to persist output; tests call it directly today,
the same shape as ``StrategyService``/``ResearchService``.

One transaction boundary only, unlike Strategy's two: BACKEND-09 has no
mutation operation analogous to ``transition_hypothesis`` — Plan Item
mutability is canonically ACKNOWLEDGED but NOT IMPLEMENTED (see
``app/planning/models.py``), so ``record_plan`` is the only write this
service exposes.

``record_plan`` is the atomic *initial* write: Content Plan + all initial
Plan Items + one AuditEvent per created entity, all in one transaction. If
anything raises before the final commit, nothing persists.

PERSISTING PLAN OUTPUT NEVER MUTATES CampaignRun.status,
RunStageExecution.status, or any HumanDecisionRequest/Response — no code
below touches any of those tables. Nothing here creates, references, or
implies a Content Brief, a Content Piece, a Strategic Decision, a Gate
Decision, a Plan Approval, or Production Authorization — those entities are
not implemented in this codebase or are out of scope for this bounded
context.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign, CampaignRun
from app.core.api_errors import ProvenanceMismatchError, VersionConflictError
from app.orchestration.models import BusinessStage, RunStageExecution
from app.planning.models import ContentPlan, PlanItem
from app.planning.repository import ContentPlanRepository, PlanItemRepository

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
                version=next_version, summary=summary,
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
