"""Tracking persistence — BACKEND-15.

No public HTTP write endpoint exists for TrackingPlan/TrackingRequirement
creation anywhere in this module — the frozen API surface (Governance
Freeze §O/TRK-D29) is GET plus one narrow, discriminated PATCH scoped to
either a Plan transition or a Requirement's own status.
``record_tracking_plan``/``record_tracking_requirement`` are service-layer-
only, exactly like every derived/computed entity in every prior stage —
callers today are tests, and in the future a separately-authorized
orchestration/agent runtime.

CAMPAIGN-SCOPED RESOURCE INTEGRITY (Governance Freeze, TRK-D15):
``transition_tracking_plan``/``update_tracking_requirement_status`` both
take an already-loaded, already-authorized ``Campaign`` — never a bare
``workspace_id`` — because active Workspace membership alone never proves
a given Plan/Requirement belongs to the Campaign named in the URL.

REQUIREMENT MUTATION LIFECYCLE (Governance Freeze-R, TRK-D36-TRK-D41):
``record_tracking_requirement`` is legal only while the parent Plan's
status is in ``REQUIREMENT_CREATION_ALLOWED_STATUSES``;
``update_tracking_requirement_status`` is legal only while it is in
``REQUIREMENT_STATUS_MUTATION_ALLOWED_STATUSES``. Once ``CERTIFIED``, both
are forbidden unconditionally — no caller, internal or public, may bypass
this. Creating/mutating a Requirement never automatically moves
``TrackingPlan.status`` in either direction; a forbidden-state attempt is
rejected before any persistence mutation and before any audit insertion.

READINESS SOURCE OF TRUTH (TRK-D02/TRK-D03): ``TrackingPlan.status`` is
the sole physical readiness field. "Tracking Status" is a read-model name
only — nothing here computes, persists, or exposes a second status value.

PROVIDER-AGNOSTIC (TRK-D23): nothing here calls, references, or implies
any external analytics/ad platform, OAuth flow, credential, or network
verification. ``CERTIFIED`` is a manual, self-declared attestation only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.core.api_errors import (
    ForbiddenError,
    InvalidLifecycleTransitionError,
    TrackingPlanAlreadyExistsError,
    TrackingRequirementMutationForbiddenError,
)
from app.tracking.models import TrackingPlan, TrackingReadinessStatus, TrackingRequirement
from app.tracking.repository import TrackingPlanRepository, TrackingRequirementRepository
from app.tracking.transitions import (
    REQUIREMENT_CREATION_ALLOWED_STATUSES,
    REQUIREMENT_STATUS_MUTATION_ALLOWED_STATUSES,
    is_legal_tracking_plan_transition,
)

EVENT_PLAN_RECORDED = "tracking.plan.recorded"
EVENT_PLAN_STATUS_CHANGED = "tracking.plan.status_changed"
EVENT_REQUIREMENT_RECORDED = "tracking.requirement.recorded"
EVENT_REQUIREMENT_STATUS_CHANGED = "tracking.requirement.status_changed"


class TrackingService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.plans = TrackingPlanRepository(session)
        self.requirements = TrackingRequirementRepository(session)
        self.events = AuditEventRepository(session)

    # --- reads (GET-only public surface calls these) -----------------

    def get_plan_for_campaign(self, *, campaign_id: uuid.UUID) -> TrackingPlan | None:
        return self.plans.get_for_campaign(campaign_id=campaign_id)

    def list_requirements_for_plan(self, *, tracking_plan_id: uuid.UUID) -> list[TrackingRequirement]:
        return self.requirements.list_for_plan(tracking_plan_id=tracking_plan_id)

    # --- TrackingPlan: service-layer only creation ----------------------

    def record_tracking_plan(
        self, *, campaign: Campaign, actor_user_id: uuid.UUID | None = None, request_id: str | None = None,
    ) -> TrackingPlan:
        """Always created at NOT_DEFINED (Governance Freeze §F) — the
        caller cannot supply an arbitrary initial status. Enforces the
        0..1 cardinality (TRK-D01): a second Plan for the same Campaign
        is a deterministic conflict, never a silent no-op/overwrite."""
        if self.plans.get_for_campaign(campaign_id=campaign.id) is not None:
            raise TrackingPlanAlreadyExistsError()

        plan = self.plans.create(campaign=campaign)

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_PLAN_RECORDED,
            actor_type=actor_type,
            tracking_plan_id=plan.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return plan

    # --- TrackingRequirement: service-layer only creation ----------------

    def record_tracking_requirement(
        self,
        *,
        tracking_plan: TrackingPlan,
        name: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> TrackingRequirement:
        """Legal only while the parent Plan's status is in
        REQUIREMENT_CREATION_ALLOWED_STATUSES (Governance Freeze-R,
        TRK-D36) — forbidden once CONFIGURED/VERIFICATION_PENDING/
        CERTIFIED, since a brand-new, never-configured-or-verified row
        would silently invalidate either assertion. Applies unconditionally
        regardless of caller (internal/system callers included, TRK-D41).
        Never mutates TrackingPlan.status."""
        if tracking_plan.status not in REQUIREMENT_CREATION_ALLOWED_STATUSES:
            raise TrackingRequirementMutationForbiddenError()

        requirement = self.requirements.create(tracking_plan=tracking_plan, name=name)

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=tracking_plan.workspace_id,
            event_type=EVENT_REQUIREMENT_RECORDED,
            actor_type=actor_type,
            tracking_plan_id=tracking_plan.id,
            tracking_requirement_id=requirement.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return requirement

    # --- TrackingPlan: one generic, campaign-scoped transition ----------

    def transition_tracking_plan(
        self,
        *,
        campaign: Campaign,
        target_status: TrackingReadinessStatus,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> TrackingPlan:
        """Campaign-scoped resource integrity (Governance Freeze, TRK-D15):
        ``campaign`` must already be resolved and authorized by the
        caller (workspace membership proven) — this method looks up the
        Plan directly by ``campaign_id`` (TrackingPlan carries it
        directly, no JOIN chain needed). One generic transition command,
        validated against the frozen graph (TRK-D05) — no per-edge
        methods. Enforces the single existence-only precondition on
        NOT_DEFINED -> REQUIREMENTS_DEFINED (TRK-D28): >=1
        TrackingRequirement must already exist; no TrackingRequirement
        status value is ever inspected."""
        plan = self.plans.get_for_campaign(campaign_id=campaign.id, for_update=True)
        if plan is None:
            raise ForbiddenError()

        if not is_legal_tracking_plan_transition(plan.status, target_status):
            raise InvalidLifecycleTransitionError(
                f"Cannot transition a tracking plan from {plan.status.value} to {target_status.value}."
            )

        if (
            plan.status is TrackingReadinessStatus.NOT_DEFINED
            and target_status is TrackingReadinessStatus.REQUIREMENTS_DEFINED
            and self.requirements.count_for_plan(tracking_plan_id=plan.id) < 1
        ):
            raise InvalidLifecycleTransitionError(
                "Cannot transition a tracking plan to REQUIREMENTS_DEFINED with zero Tracking Requirements."
            )

        previous = plan.status
        plan.status = target_status

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_PLAN_STATUS_CHANGED,
            actor_type=actor_type,
            tracking_plan_id=plan.id,
            previous_state=previous.value,
            new_state=target_status.value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return plan

    # --- TrackingRequirement: campaign-scoped status mutation -----------

    def update_tracking_requirement_status(
        self,
        *,
        campaign: Campaign,
        requirement_public_id: str,
        status: str | None,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> TrackingRequirement:
        """Campaign-scoped, non-leaky lookup (Governance Freeze, TRK-D15)
        joined through TrackingPlan.campaign_id. Legal only while the
        parent Plan's status is in
        REQUIREMENT_STATUS_MUTATION_ALLOWED_STATUSES (Governance Freeze-R,
        TRK-D37) — forbidden only once CERTIFIED, freezing both the
        Requirement set and every existing Requirement's status
        (TRK-D38). ``status`` may be explicitly cleared back to NULL
        (TRK-D27) — purely descriptive, never itself a member of any
        enum, never automatically transitions TrackingPlan.status."""
        requirement = self.requirements.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=requirement_public_id, for_update=True
        )
        if requirement is None:
            raise ForbiddenError()

        plan = self.plans.get_for_campaign(campaign_id=campaign.id)
        if plan is None or plan.status not in REQUIREMENT_STATUS_MUTATION_ALLOWED_STATUSES:
            raise TrackingRequirementMutationForbiddenError()

        requirement.status = status

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_REQUIREMENT_STATUS_CHANGED,
            actor_type=actor_type,
            tracking_plan_id=plan.id,
            tracking_requirement_id=requirement.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return requirement
