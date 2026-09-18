"""Planning bounded context — BACKEND-09, extended by MVP-33B (frozen
MVP-33A/-33A-R1 contract).

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
for BOOTSTRAP-origin rows only — which run and which PLAN stage-execution
instance produced this version — never as the ownership relationship.
ContentPlan is versioned and immutable once created, matching Strategy/
ResearchReport/AudienceProfile's own precedent: no ``status``, ``approved``,
or ``ready_for_production`` field — "current" = ``MAX(version)`` for the
campaign.

**ORIGIN (MVP-33A §K/MVP-33A-R1 §K, frozen):** ``origin`` describes
CREATION MECHANISM only, never actor identity — ``GOVERNED != HUMAN``,
exactly mirroring ``StrategyOrigin``'s own BOOTSTRAP/REVISION precedent one
context over. ``BOOTSTRAP`` rows are created by the deterministic
orchestration bootstrap and always carry a real ``campaign_run_id`` +
``stage_execution_id`` (the PLAN stage-execution instance that produced
them). ``GOVERNED`` rows are created through the governed ContentPlan write
contract; ``campaign_run_id``/``stage_execution_id`` are always NULL for
them — not "not yet known," but structurally absent, since MVP-33A-R1 §F
proved no human-reachable, not-yet-consumed CampaignRun/PLAN-StageExecution
instance can ever exist (the bootstrap consumes the only one synchronously
inside ``start_run``). Enforced by ``ck_content_plans_origin_bootstrap_fields``
below. Actor identity (human today, potentially agent-assisted later) is
recorded exclusively on the associated ``AuditEvent``, never encoded here.

**EXPERIMENT PROVENANCE (MVP-33A §K/§L, frozen):** ``experiment_id`` is a
nullable, optional reference — ``ContentPlan N -> 0..1 Experiment``. NULL
means "this Plan makes no Experiment claim" (Case G, always true for
BOOTSTRAP rows — enforced by ``ck_content_plans_bootstrap_experiment_null``
below, since the bootstrap synthesizer discards Hypothesis/Experiment data
before Plan synthesis; see ``app/orchestration/service.py::_write_plan``).
A non-NULL value makes exactly one claim: "this ContentPlan was created to
operationalize Experiment E" — nothing about Variant, execution readiness,
measurement readiness, or winner declaration (MVP-33A §AA). Historical-
Strategy Experiments remain eligible (MVP-33A §N: E2 ALLOWED) — no Strategy
currency recheck happens at Plan-creation time, no lock, no retroactive
invalidation. Same-Workspace is DATABASE-enforced via the composite tenant
FK below; same-Campaign is SERVICE-enforced only (MVP-33A-R1 §D/§E) — no
``campaign_id`` column exists on ``Experiment`` (nor is one added — denormal-
izing it was explicitly evaluated and rejected as over-hardening, MVP-33A-R1
§F), so Campaign ancestry is proven by a repository-level JOIN through
Hypothesis -> Strategy, never assumed from the FK's mere existence (the
same discipline already applied to every other cross-entity provenance
check in this codebase).

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

No structural FK to Strategy or Positioning exists on either model — that
remains a documented, carried-forward traceability gap, explicitly NOT
repaired here (MVP-33A §R/MVP-33A-R1: out of scope; Case E ancestry to
Strategy remains reconstructable only by walking Experiment -> Hypothesis
-> Strategy when ``experiment_id`` is set, never a direct claim). STRATEGY
PERSISTED != READY FOR PLANNING. STRATEGY != CONTENT PLAN. The one
exception, added by MVP-33B, is the optional, nullable ``experiment_id``
described above — BACKEND-01's original ER diagram predates any Experiment-
provenance authorization; this addition is scoped narrowly to that single
relation and does not otherwise alter ContentPlan's Campaign-sibling
ownership shape.

No field or table anywhere below implies Plan approval, Plan Item approval,
production authorization, distribution readiness, paid execution, or a
chain-of-thought/reasoning trace. PLAN PERSISTED != PLAN APPROVED. PLAN ITEM
PERSISTED != PRODUCTION AUTHORIZED. READY FOR PLANNING != READY FOR
PRODUCTION.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, UUIDPrimaryKeyMixin

_FORMAT_MAX_LENGTH = 100
_OBJECTIVE_MAX_LENGTH = 1000


class ContentPlanOrigin(str, enum.Enum):
    """MVP-33A §K/MVP-33A-R1 §K frozen vocabulary — exactly the two legal
    origins. Describes creation MECHANISM only, never actor identity;
    GOVERNED != HUMAN (see module docstring "ORIGIN"). No speculative
    future value (HUMAN/AGENT/SYSTEM/REVISION/EXPERIMENT) is added here."""

    BOOTSTRAP = "BOOTSTRAP"
    GOVERNED = "GOVERNED"


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
        # BACKEND-10 (explicitly authorized, additive-only — see
        # app/content/models.py's own module docstring): a candidate key
        # purely so ContentBrief can declare a composite tenant-safety FK
        # on (content_plan_id, workspace_id). This does not change
        # ContentPlan's ownership, tenancy, versioning, or any other
        # BACKEND-09 semantic — it is the same additive-candidate-key
        # pattern BACKEND-08 already used for Strategy/Hypothesis.
        UniqueConstraint("id", "workspace_id", name="uq_content_plans_id_workspace_id"),
        # MVP-33B: the DB-enforced half of the origin invariant (see module
        # docstring "ORIGIN") — mirrors ck_strategies_origin_bootstrap_fields
        # exactly, one context over. BOOTSTRAP rows always carry both
        # provenance columns; GOVERNED rows always carry neither.
        CheckConstraint(
            "(origin = 'BOOTSTRAP' AND campaign_run_id IS NOT NULL AND stage_execution_id IS NOT NULL) OR "
            "(origin = 'GOVERNED' AND campaign_run_id IS NULL AND stage_execution_id IS NULL)",
            name="origin_bootstrap_fields",
        ),
        # MVP-33B (MVP-33A-R1 §N, firmly frozen, not "recommended"): a
        # BOOTSTRAP row can never carry Experiment provenance — the
        # deterministic bootstrap synthesizer discards Hypothesis/Experiment
        # data before Plan synthesis (app/orchestration/service.py::
        # _write_plan), so a non-NULL experiment_id on a BOOTSTRAP row would
        # be fabricated provenance. GOVERNED rows may be NULL (Case G) or
        # set (Case E) — both legitimate.
        CheckConstraint(
            "origin != 'BOOTSTRAP' OR experiment_id IS NULL",
            name="bootstrap_experiment_null",
        ),
        # MVP-33B: composite tenant-safe FK to Experiment — same-Workspace
        # DB-enforced; same-Campaign is SERVICE-enforced only (module
        # docstring "EXPERIMENT PROVENANCE", MVP-33A-R1 §D/§E) since
        # Experiment carries no campaign_id column and none is added here.
        ForeignKeyConstraint(
            ["experiment_id", "workspace_id"],
            ["experiments.id", "experiments.workspace_id"],
            name="fk_content_plans_experiment_workspace",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    origin: Mapped[ContentPlanOrigin] = mapped_column(Enum(ContentPlanOrigin, name="content_plan_origin", native_enum=True))
    # Nullable — see module docstring "ORIGIN" and the CHECK constraint
    # above; NULL for GOVERNED-origin rows, required together for
    # BOOTSTRAP-origin rows.
    campaign_run_id: Mapped[uuid.UUID | None] = mapped_column(default=None, index=True)  # covered by the composite FK above
    # Plain FK only — the semantic check ("this stage_execution belongs to
    # this exact campaign_run and is the PLAN stage") is proven in the
    # service layer, not assumed from the FK's mere existence. Nullable —
    # see origin CHECK above.
    stage_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("run_stage_executions.id"), default=None, index=True
    )
    # Nullable, optional Experiment provenance — see module docstring
    # "EXPERIMENT PROVENANCE" and the bootstrap_experiment_null CHECK above.
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(default=None, index=True)  # covered by the composite FK above
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
