"""Planning bounded context — BACKEND-09.

Persists exactly the two entities BACKEND-01 canonically assigns to the
``planning`` module (`docs/backend/BACKEND-01-ARCHITECTURE.md` §1:
"Content Plan, Plan Item") and no others. Everything else that reads like a
plausible Planning entity — Content Brief, Content Piece, Content Version,
Content Approval, Creative Brief, Asset, Distribution Plan, Paid Media Plan —
is bounded-context-owned elsewhere (`content`, `assets`, `distribution`,
`paid_media`) and is deliberately not modeled here. PLAN ITEM != CONTENT
BRIEF. PLAN ITEM != CONTENT PIECE. PLANNING != CONTENT PRODUCTION.

Ownership mirrors ``app/strategy/models.py``'s ``Strategy`` exactly for
``ContentPlan``: Campaign-owned (sibling of Strategy/ResearchReport/
AudienceProfile/Orchestration Run under Campaign), with
``campaign_run_id``/``stage_execution_id`` carried as required *provenance*
— which run and which PLAN stage-execution instance produced this version —
never as the ownership relationship. ContentPlan is versioned and immutable
once created, matching Strategy/ResearchReport/AudienceProfile's own
precedent: no ``status``, ``approved``, or ``ready_for_production`` field —
"current" = ``MAX(version)`` for the campaign.

PlanItem is deliberately shaped like ``Positioning``, not like ``Hypothesis``/
``Experiment``: BACKEND-01 annotates its tenant column as "Workspace **(via
Plan)**" — the same qualified annotation Positioning gets for Strategy — so
it carries no direct ``workspace_id`` of its own; tenancy is reached only by
traversal through its parent ``ContentPlan``. This is a deliberate structural
choice, not an oversight: adding a direct ``workspace_id`` to PlanItem would
contradict BACKEND-01's own qualifier.

BACKEND-01 also describes PlanItem as "Mutable? Yes (until briefed)" but
names no mutation fields, no lifecycle vocabulary, and no schema mechanism
for "briefed" — that state is reached only by a Content Brief existing, and
Content Brief is out of scope for BACKEND-09 (`content` bounded context).
Inventing a status/lifecycle column here to represent an undefined state
would fabricate semantics BACKEND-01 never specified, exactly the discipline
already applied to ``Experiment.status`` in BACKEND-08. Accordingly: this
module persists PlanItem rows with their initial values only. Canonical
mutability is ACKNOWLEDGED but NOT IMPLEMENTED — no update column, no
``updated_at``, no mutation method exists anywhere in this module. PlanItem
here is not claimed to be canonically immutable; it is simply not yet
mutable in this stage, pending an explicitly authorized future lifecycle
definition.

No structural FK to Strategy/Positioning/Hypothesis/Experiment exists on
either model. BACKEND-01 defines no such relationship (the ER diagram draws
Content Plan as a direct sibling of Strategy under Campaign, not a child of
it) — this is a documented, carried-forward traceability gap, not repaired
by invention here. STRATEGY PERSISTED != READY FOR PLANNING. STRATEGY !=
CONTENT PLAN.

No field or table anywhere below implies Plan approval, Plan Item approval,
production authorization, distribution readiness, paid execution, or a
chain-of-thought/reasoning trace. PLAN PERSISTED != PLAN APPROVED. PLAN ITEM
PERSISTED != PRODUCTION AUTHORIZED. READY FOR PLANNING != READY FOR
PRODUCTION.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, UUIDPrimaryKeyMixin

_FORMAT_MAX_LENGTH = 100
_OBJECTIVE_MAX_LENGTH = 1000


class ContentPlan(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "content_plans"
    __table_args__ = (
        UniqueConstraint("campaign_id", "version", name="uq_content_plans_campaign_version"),
        # Ownership: workspace-safe by construction — reuses the existing
        # `uq_campaigns_id_workspace_id` candidate key, no Campaign schema
        # change (mirrors app/strategy/models.py::Strategy exactly).
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_content_plans_campaign_workspace",
        ),
        # Provenance: which run, workspace-safe the same way — reuses the
        # BACKEND-07-authorized `CampaignRun(id, workspace_id)` candidate
        # key. Does not by itself prove the run belongs to *this* campaign
        # or that the stage_execution is the PLAN stage of that run — those
        # checks are service-layer (app/planning/service.py), not
        # database-layer, same reasoning as Strategy/Research.
        ForeignKeyConstraint(
            ["campaign_run_id", "workspace_id"],
            ["campaign_runs.id", "campaign_runs.workspace_id"],
            name="fk_content_plans_campaign_run_workspace",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    campaign_run_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    # Plain FK only — the semantic check ("this stage_execution belongs to
    # this exact campaign_run and is the PLAN stage") is proven in the
    # service layer, not assumed from the FK's mere existence.
    stage_execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("run_stage_executions.id"), index=True)
    version: Mapped[int] = mapped_column()
    # BACKEND-01: "AGENT-04's structured calendar/plan for a Campaign" — a
    # single narrative field, matching Strategy.summary/ResearchReport.
    # summary's own precedent, not a set of separately structured columns
    # BACKEND-01 never itemizes at the Content Plan level.
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanItem(Base, UUIDPrimaryKeyMixin):
    """Belongs to exactly one ``ContentPlan`` — no direct ``workspace_id``
    column, tenant reached only by traversal through the parent Plan,
    matching BACKEND-01's own words for Plan Item: "Workspace (via Plan)"
    (the same "via parent" pattern already used by
    ``Positioning``/``ResearchSource``/``CampaignBrief``)."""

    __tablename__ = "plan_items"

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    content_plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_plans.id"), index=True)
    # No canonical vocabulary is named by BACKEND-01 for either field — a
    # plain bounded string, not a native enum, the same "do not invent an
    # unnamed vocabulary" discipline already applied to Experiment.status
    # in BACKEND-08.
    format: Mapped[str] = mapped_column(String(_FORMAT_MAX_LENGTH))
    objective: Mapped[str] = mapped_column(String(_OBJECTIVE_MAX_LENGTH))
    # Immutable, persisted ordering — never derived from insertion order
    # (same convention as RunStageExecution.ordinal).
    sequence: Mapped[int] = mapped_column()
    # A planning target date only — no time-of-day, no timezone, no
    # recurrence, no implication of external scheduling or publication
    # (BACKEND-09 §11/§17). Deliberately not named `publish_at`.
    scheduled_date: Mapped[date | None] = mapped_column(Date, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
