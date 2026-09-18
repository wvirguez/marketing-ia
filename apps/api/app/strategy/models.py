"""Strategy bounded context — BACKEND-08.

Persists exactly the four entities BACKEND-01 canonically assigns to the
``strategy`` module (`docs/backend/BACKEND-01-ARCHITECTURE.md` §1:
"Strategy, Positioning, Hypothesis, Experiment") and no others. Two
BACKEND-01 terms that read like plausible Strategy entities are
deliberately NOT modeled here: ``Strategic Recommendation Candidate``
belongs to the ``learning`` bounded context (it is produced only from a
Validated Learning, per the domain-model entity catalog, and is served by
the separate `/campaigns/{id}/learning` route) and ``Strategic Decision``
belongs conceptually to ``orchestration`` (the ER diagram hangs it off
Orchestration Run, not Strategy, and it may originate from a Gate Decision
that does not exist in this codebase). Neither is implemented here, and
nothing below stands in as a substitute for either.

Ownership mirrors ``app/research/models.py`` exactly for ``Strategy``:
Campaign-owned (sibling of ResearchReport/AudienceProfile/Orchestration Run
under Campaign). Strategy is versioned and immutable once created, matching
ResearchReport/AudienceProfile's own precedent exactly: no ``status``,
``ACTIVE``, ``SUPERSEDED``, or ``approved`` field — "current" =
``MAX(version)`` for the campaign.

ORIGIN (MVP-30A-R1, frozen): every Strategy row has exactly one of two
legal origins, recorded explicitly in ``origin`` rather than left as tribal
knowledge inferable only from column nullability:

- ``BOOTSTRAP`` — created by the deterministic bootstrap
  (``StrategyService.record_strategy``, called only from
  ``app/orchestration/service.py``'s own bootstrap). Carries required
  *provenance* — ``campaign_run_id``/``stage_execution_id`` — which run and
  STRATEGY-stage execution instance produced this version.
- ``REVISION`` — created by a governed Strategy Revision
  (``app/orchestration/service.py::StrategyRevisionService``, MVP-30B). Has
  no live ``CampaignRun``/``RunStageExecution`` context at all (a Revision
  can happen long after any particular run's own STRATEGY stage completed),
  so both provenance columns are NULL. The governance provenance for *why*
  a REVISION-origin row exists — which ``StrategicApproval`` authorized it,
  which prior Strategy it revised — is never stored here; it lives entirely
  in ``StrategyRevision`` (``app/orchestration/models.py``), consistent with
  this module's own long-standing disclaimer below that no Strategic
  Decision/Approval concept is implemented in ``app/strategy/``.

The ``ck_strategies_origin_bootstrap_fields`` CHECK constraint below is the
DB-enforced half of this invariant (BOOTSTRAP ⇔ both provenance columns
NOT NULL; REVISION ⇔ both NULL). The other half — that a REVISION-origin
row is always actually referenced by exactly one ``StrategyRevision`` — is
NOT independently DB-enforceable (no plain FK/CHECK can express "some row
elsewhere must reference this row" without a trigger or a circular FK, both
deliberately not introduced, MVP-30A-R1 §I). It is instead
TRANSACTIONALLY GUARANTEED: the only production code path that can ever
insert a REVISION-origin row (``StrategyRevisionService.revise_strategy``)
inserts it and its corresponding ``StrategyRevision`` row inside the same
uncommitted transaction — if either insert fails, both roll back together,
so no orphan REVISION-origin row can ever become visible to any other
transaction.

Positioning is a 1:1 immutable child of Strategy with no direct tenant
column of its own — BACKEND-01's domain-model catalog annotates its tenant
as "Workspace (via Strategy)" (unlike Hypothesis/Experiment below, which
get an un-qualified "Workspace"), the same "via parent" pattern already
used by ``ResearchSource``/``CampaignBrief``. It has no independent
``version``/``status``/``approval`` — it "follows Strategy version."

Hypothesis and Experiment are new ground for this codebase: BACKEND-01
annotates both with a *direct*, un-qualified "Workspace" tenant column even
though each belongs to a single parent (Strategy, Hypothesis respectively)
— so each carries its own ``workspace_id``, defended by a composite FK
against its parent's own ``(id, workspace_id)`` candidate key, the same
mechanism ``CampaignRun``/``Strategy``/``Hypothesis`` each already use one
level up. Both are also genuinely mutable (a ``status`` concept), not
versioned — closer in shape to ``RunStageExecution`` than to
``ResearchReport``.

HYPOTHESIS != FACT. CONFIRMED HYPOTHESIS != VALIDATED LEARNING. Nothing
below creates, references, or implies a Learning Candidate — that entity
belongs to the ``learning`` bounded context and does not exist in this
codebase (BACKEND-08 §10/§20).

Experiment's status vocabulary is NOT canonically defined by BACKEND-01 —
the domain model states only that Experiment "is mutable (status)" without
naming any values. Inventing enum values would fabricate semantics
BACKEND-01 never specified, so ``Experiment.status`` is a plain nullable
string, not a native enum, and no service-layer transition operation exists
for it in this stage.

No field or table anywhere below implies Strategy approval, target
approval, positioning approval, a Strategic Decision, a Gate Decision,
planning readiness, execution readiness, or a chain-of-thought/reasoning
trace. STRATEGY PERSISTED != APPROVED STRATEGY. STRATEGY PERSISTED != READY
FOR PLANNING.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

_STATEMENT_MAX_LENGTH = 4000
_DESCRIPTION_MAX_LENGTH = 4000
_EXPERIMENT_STATUS_MAX_LENGTH = 30


class StrategyOrigin(str, enum.Enum):
    """MVP-30A-R1 frozen vocabulary — exactly the two legal Strategy
    origins (see module docstring "ORIGIN"). No speculative future value
    is added here."""

    BOOTSTRAP = "BOOTSTRAP"
    REVISION = "REVISION"


class Strategy(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "strategies"
    __table_args__ = (
        UniqueConstraint("campaign_id", "version", name="uq_strategies_campaign_version"),
        # Ownership: workspace-safe by construction — reuses the existing
        # `uq_campaigns_id_workspace_id` candidate key, no Campaign schema
        # change (BACKEND-08 §7, mirrors app/research/models.py exactly).
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_strategies_campaign_workspace",
        ),
        # Provenance: which run, workspace-safe the same way — reuses the
        # BACKEND-07-authorized `CampaignRun(id, workspace_id)` candidate
        # key. Does not by itself prove the run belongs to *this* campaign
        # or that the stage_execution is the STRATEGY stage of that run —
        # those checks are service-layer (app/strategy/service.py), not
        # database-layer (BACKEND-08 §7, same reasoning as BACKEND-07 §10).
        # Nullable for REVISION-origin rows (MVP-30A-R1) — a composite FK
        # with MATCH SIMPLE (PostgreSQL's default) is simply not checked
        # when either referencing column is NULL.
        ForeignKeyConstraint(
            ["campaign_run_id", "workspace_id"],
            ["campaign_runs.id", "campaign_runs.workspace_id"],
            name="fk_strategies_campaign_run_workspace",
        ),
        # BACKEND-08 §9 (explicitly authorized, additive-only): a candidate
        # key purely so Hypothesis can declare a composite FK on
        # (strategy_id, workspace_id), the same pattern
        # `uq_campaigns_id_workspace_id`/`uq_campaign_runs_id_workspace_id`
        # already provide one level up. MVP-30A-R1 also reuses this exact
        # key for StrategyRevision's own base_strategy_id/result_strategy_id
        # composite FKs (app/orchestration/models.py).
        UniqueConstraint("id", "workspace_id", name="uq_strategies_id_workspace_id"),
        # MVP-30A-R1: the DB-enforced half of the origin invariant (see
        # module docstring "ORIGIN") — BOOTSTRAP rows always carry both
        # provenance columns, REVISION rows always carry neither. The
        # companion invariant ("a REVISION row is always referenced by
        # exactly one StrategyRevision") cannot be expressed this way — see
        # the docstring for why, and app/orchestration/service.py for the
        # transactional guarantee that stands in for it.
        CheckConstraint(
            "(origin = 'BOOTSTRAP' AND campaign_run_id IS NOT NULL AND stage_execution_id IS NOT NULL) OR "
            "(origin = 'REVISION' AND campaign_run_id IS NULL AND stage_execution_id IS NULL)",
            name="origin_bootstrap_fields",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    origin: Mapped[StrategyOrigin] = mapped_column(Enum(StrategyOrigin, name="strategy_origin", native_enum=True))
    # Nullable — see module docstring "ORIGIN" and the CHECK constraint
    # above; NULL for REVISION-origin rows, required together for
    # BOOTSTRAP-origin rows.
    campaign_run_id: Mapped[uuid.UUID | None] = mapped_column(default=None, index=True)  # covered by the composite FK above
    # Plain FK only — the semantic check ("this stage_execution belongs to
    # this exact campaign_run and is the STRATEGY stage") is proven in the
    # service layer, not assumed from the FK's mere existence.
    stage_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("run_stage_executions.id"), default=None, index=True
    )
    version: Mapped[int] = mapped_column()
    # BACKEND-01: "AGENT-03's structured strategy (objective, message,
    # funnel)" — a single narrative field, matching ResearchReport.summary/
    # AudienceProfile.summary's own precedent, not a set of separately
    # structured objective/message/funnel/CTA columns BACKEND-01 never
    # itemizes as distinct persisted fields.
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Positioning(Base, UUIDPrimaryKeyMixin):
    """Belongs to exactly one ``Strategy`` — no direct ``workspace_id``/
    ``campaign_id`` column, tenant reached only by traversal through the
    parent Strategy, matching BACKEND-01's own words for Positioning:
    "Workspace (via Strategy)" (the same "via parent" pattern already used
    by ``ResearchSource``/``CampaignBrief``). Exactly one row per Strategy
    (BACKEND-01: "belongs to Strategy", 1:1 in the ER diagram) — enforced
    by the unique ``strategy_id`` below."""

    __tablename__ = "positionings"

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("strategies.id"), unique=True, index=True)
    statement: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HypothesisStatus(str, enum.Enum):
    """Exact BACKEND-01 vocabulary (domain-model entity catalog: "status:
    open/confirmed/refuted") — no other value exists, and no
    reopen/reconfirm edge is authorized (see
    ``app/strategy/transitions.py``)."""

    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    REFUTED = "REFUTED"


class Hypothesis(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Belongs to exactly one ``Strategy``. Unlike ``Positioning``,
    BACKEND-01 annotates this entity's tenant column as un-qualified
    "Workspace" (no "via Strategy" qualifier) — so it carries its own
    direct ``workspace_id``, defended by a composite FK against
    ``Strategy``'s own ``(id, workspace_id)`` candidate key. Mutable
    (``status`` only) but never versioned — HYPOTHESIS != FACT; a status of
    ``CONFIRMED`` records only that this specific assumption was judged
    supported, never that a Learning Candidate now exists (that entity
    belongs to the ``learning`` bounded context, not implemented here)."""

    __tablename__ = "hypotheses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["strategy_id", "workspace_id"],
            ["strategies.id", "strategies.workspace_id"],
            name="fk_hypotheses_strategy_workspace",
        ),
        # BACKEND-08 §11 (explicitly authorized, additive-only): a
        # candidate key purely so Experiment can declare a composite FK on
        # (hypothesis_id, workspace_id) — the same pattern one level up.
        UniqueConstraint("id", "workspace_id", name="uq_hypotheses_id_workspace_id"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    strategy_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    statement: Mapped[str] = mapped_column(String(_STATEMENT_MAX_LENGTH))
    status: Mapped[HypothesisStatus] = mapped_column(
        Enum(HypothesisStatus, name="hypothesis_status", native_enum=True),
        default=HypothesisStatus.OPEN,
        server_default=HypothesisStatus.OPEN.value,
    )


class Experiment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Belongs to exactly one ``Hypothesis``. Same direct-tenant pattern as
    Hypothesis (BACKEND-01: un-qualified "Workspace"). ``status`` is a
    plain, bounded, nullable string — not a native enum — because
    BACKEND-01 states only that Experiment "is mutable (status)" without
    ever naming a status vocabulary; inventing one here would fabricate
    semantics BACKEND-01 never specified (BACKEND-08 §12). No
    ``content_piece_id``/``paid_media_plan_id`` column exists yet — those
    target tables do not exist in this codebase; a nullable FK to a
    nonexistent table is not possible, and a same-named speculative column
    with no constraint would be a schema without a contract."""

    __tablename__ = "experiments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["hypothesis_id", "workspace_id"],
            ["hypotheses.id", "hypotheses.workspace_id"],
            name="fk_experiments_hypothesis_workspace",
        ),
        # MVP-33B (explicitly authorized, additive-only, MVP-33A-R1 §F/§P):
        # a candidate key purely so ContentPlan can declare a composite
        # tenant-safety FK on (experiment_id, workspace_id) — the same
        # additive-candidate-key pattern already used repeatedly
        # (uq_strategies_id_workspace_id, uq_hypotheses_id_workspace_id,
        # uq_content_plans_id_workspace_id). Does not change Experiment's
        # own ownership, tenancy, or any other MVP-32 semantic. Same-
        # Campaign ancestry is deliberately NOT enforced here — Experiment
        # gains no campaign_id column (MVP-33A-R1 §F: denormalizing it was
        # evaluated and rejected as over-hardening); Campaign-scoping
        # remains a SERVICE-layer JOIN-through-Hypothesis/Strategy
        # invariant, identical in kind to Hypothesis's own lack of a direct
        # campaign_id column.
        UniqueConstraint("id", "workspace_id", name="uq_experiments_id_workspace_id"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    hypothesis_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    description: Mapped[str] = mapped_column(String(_DESCRIPTION_MAX_LENGTH))
    # No canonical vocabulary and no default — BACKEND-01 does not state
    # Experiment's initial value either (unlike Hypothesis's explicit
    # "creation default: OPEN"). Left NULL until a future stage with real
    # canonical status semantics assigns one.
    status: Mapped[str | None] = mapped_column(String(_EXPERIMENT_STATUS_MAX_LENGTH), default=None)
