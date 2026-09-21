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

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class ComparisonType(str, enum.Enum):
    """MVP-37 (frozen MVP-37B §C): the coarsest possible declared-intent
    vocabulary. ``CONTROLLED`` is a declared design intent only — it is
    never a validated experimental status, and no randomized/
    non-randomized/sequential value exists (those belong to a future
    allocation contract). Stored as a plain ``String(20)`` guarded by a
    named CHECK, not a native PostgreSQL enum."""

    OBSERVATIONAL = "OBSERVATIONAL"
    CONTROLLED = "CONTROLLED"


EXPERIMENT_DEFINITION_PROSE_MAX_LENGTH = 1000
EXPERIMENT_DEFINITION_FACTOR_MAX_LENGTH = 200
EXPERIMENT_DEFINITION_MAX_CONTROLLED_FACTORS = 20
EXPERIMENT_DEFINITION_CLIENT_REQUEST_ID_MAX_LENGTH = 100


class ExperimentDefinitionVersion(Base, UUIDPrimaryKeyMixin):
    """MVP-37 (frozen MVP-37A/-37B): one immutable, append-only version of
    an Experiment's declared comparison design. The logical "definition"
    is the Experiment's own slot; this table holds its N versions
    (``version`` starts at 1; the current tip is the highest ordinal).

    DECLARATION != PRE-REGISTRATION. DEFINITION != VALID EXPERIMENT.
    DECLARED_CONTROLLED_INTENT != CONTROLLED EXPERIMENT. Nothing here
    represents a measurement contract, a Variant, allocation, execution
    authorization, a result or a winner — no such column exists.

    Immutability is structural by absence: no ``updated_at``, no
    ``created_by`` (the audit event carries the actor), no repository
    update/delete method. Version contiguity (no gaps) and the element
    type of ``controlled_factors`` are SERVICE-LAYER invariants only
    (MVP37B-OBS-1) — no PostgreSQL CHECK can inspect JSONB elements and
    no self-FK expresses "version N-1 exists".

    Ownership: direct ``workspace_id`` plus a composite tenant-safe FK
    against ``experiments(id, workspace_id)``. No ``campaign_id`` — Campaign
    scoping stays a service-layer join (MVP-33A-R1 §F).

    Definition lock (MVP-38): an ``ExperimentVariant`` pins one version and
    its existence blocks further versions. The lock is DERIVED (never
    stored) and enforced at the single writer choke point
    ``ExperimentDefinitionService.write_version`` under the canonical
    Strategy-then-Experiment lock order. Experiment Definition governance
    owns the lock; Variant is only its first implemented trigger."""

    __tablename__ = "experiment_definition_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_id", "workspace_id"],
            ["experiments.id", "experiments.workspace_id"],
            name="fk_experiment_definition_versions_experiment_workspace",
        ),
        UniqueConstraint("experiment_id", "version", name="uq_experiment_definition_versions_experiment_version"),
        # MVP-38: candidate key purely so ``experiment_variants`` (and any
        # future pinning child) can declare a composite tenant- and
        # Experiment-safe FK on (definition_version_id, experiment_id,
        # workspace_id). ``id`` is already unique, so every existing row
        # satisfies it — no backfill.
        UniqueConstraint(
            "id", "experiment_id", "workspace_id", name="uq_experiment_definition_versions_id_experiment_workspace"
        ),
        UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_experiment_definition_versions_workspace_client_request_id"
        ),
        CheckConstraint("comparison_type IN ('OBSERVATIONAL', 'CONTROLLED')", name="comparison_type_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("jsonb_typeof(controlled_factors) = 'array'", name="controlled_factors_is_array"),
        CheckConstraint(
            "CASE WHEN jsonb_typeof(controlled_factors) = 'array' "
            "THEN jsonb_array_length(controlled_factors) <= 20 ELSE false END",
            name="controlled_factors_max_count",
        ),
        CheckConstraint(
            "comparison_type <> 'CONTROLLED' OR (CASE WHEN jsonb_typeof(controlled_factors) = 'array' "
            "THEN jsonb_array_length(controlled_factors) >= 1 ELSE false END)",
            name="controlled_needs_factors",
        ),
        CheckConstraint(
            "char_length(btrim(comparison_question)) > 0 AND char_length(btrim(changed_factor)) > 0 "
            "AND char_length(btrim(comparison_basis)) > 0 AND char_length(btrim(scope)) > 0 "
            "AND char_length(btrim(learning_intent)) > 0 AND char_length(btrim(non_conclusion_boundary)) > 0",
            name="text_fields_nonblank",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    version: Mapped[int] = mapped_column(Integer)
    comparison_question: Mapped[str] = mapped_column(String(EXPERIMENT_DEFINITION_PROSE_MAX_LENGTH))
    comparison_type: Mapped[str] = mapped_column(String(20))
    changed_factor: Mapped[str] = mapped_column(String(EXPERIMENT_DEFINITION_FACTOR_MAX_LENGTH))
    controlled_factors: Mapped[list] = mapped_column(JSONB)
    comparison_basis: Mapped[str] = mapped_column(String(EXPERIMENT_DEFINITION_PROSE_MAX_LENGTH))
    scope: Mapped[str] = mapped_column(String(EXPERIMENT_DEFINITION_PROSE_MAX_LENGTH))
    learning_intent: Mapped[str] = mapped_column(String(EXPERIMENT_DEFINITION_PROSE_MAX_LENGTH))
    non_conclusion_boundary: Mapped[str] = mapped_column(String(EXPERIMENT_DEFINITION_PROSE_MAX_LENGTH))
    client_request_id: Mapped[str] = mapped_column(String(EXPERIMENT_DEFINITION_CLIENT_REQUEST_ID_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


EXPERIMENT_VARIANT_LABEL_MAX_LENGTH = 200
EXPERIMENT_VARIANT_DESCRIPTION_MAX_LENGTH = 1000


class ExperimentVariant(Base, UUIDPrimaryKeyMixin):
    """MVP-38 (frozen MVP-38A/-38B): the immutable governed identity of ONE
    declared condition of one Experiment, bound to ONE immutable
    ``ExperimentDefinitionVersion``.

    VARIANT IDENTITY != ALLOCATION != RANDOMIZATION != EXPOSURE !=
    MEASUREMENT != RESULT != WINNER != CAUSALITY != EXECUTION
    AUTHORIZATION. Nothing here is a role (control/treatment), a weight, a
    metric, a criterion, a status, or a link to content, distribution,
    evidence or commercial outcomes — no such column exists.

    Immutable and append-only by absence: no ``updated_at``, no
    ``created_by`` (the audit event carries the actor), no repository
    update/delete method, no correction, supersession or retirement.
    CURRENT MVP-38 CAPABILITY LIMIT: an erroneous committed Variant cannot
    be repaired in place because no governed correction/invalidation path
    exists yet (MVP38A-OBS-3).

    Parentage: composite FK ``(definition_version_id, experiment_id,
    workspace_id)`` to ``experiment_definition_versions`` — the database
    proves the Variant's Experiment and workspace equal the pinned
    version's. ``ordinal`` is the 1-based declaration order within the
    pinned version, assigned under the Experiment row lock
    (``created_at`` is transaction-start time and is NOT authoritative
    order). There is deliberately NO maximum number of Variants.

    Normalized-label uniqueness (casefold/whitespace-collapsed) is enforced
    by the service under the Experiment lock; the database backstops only
    the exact string (MVP38A-OBS-1)."""

    __tablename__ = "experiment_variants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["definition_version_id", "experiment_id", "workspace_id"],
            [
                "experiment_definition_versions.id",
                "experiment_definition_versions.experiment_id",
                "experiment_definition_versions.workspace_id",
            ],
            name="fk_experiment_variants_definition_version_experiment_workspace",
        ),
        UniqueConstraint("definition_version_id", "label", name="uq_experiment_variants_definition_version_label"),
        UniqueConstraint("definition_version_id", "ordinal", name="uq_experiment_variants_definition_version_ordinal"),
        UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_experiment_variants_workspace_client_request_id"
        ),
        # MVP-40: candidate key purely so ``execution_authorization_variants``
        # can declare a composite tenant- and Experiment-safe FK on
        # (variant_id, experiment_id, workspace_id) — the exact same
        # "give it a real candidate key the moment a composite FK is
        # foreseeable" pattern already applied to
        # ``experiment_definition_versions``/``measurement_contract_versions``.
        # ``id`` is already unique, so every existing row satisfies it — no
        # backfill.
        UniqueConstraint(
            "id", "experiment_id", "workspace_id", name="uq_experiment_variants_id_experiment_workspace"
        ),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint(
            "char_length(btrim(label)) > 0 AND char_length(btrim(condition_description)) > 0",
            name="text_fields_nonblank",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    definition_version_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    ordinal: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(EXPERIMENT_VARIANT_LABEL_MAX_LENGTH))
    condition_description: Mapped[str] = mapped_column(String(EXPERIMENT_VARIANT_DESCRIPTION_MAX_LENGTH))
    client_request_id: Mapped[str] = mapped_column(String(EXPERIMENT_DEFINITION_CLIENT_REQUEST_ID_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH = 1000
MEASUREMENT_CONTRACT_SIGNAL_NAME_MAX_LENGTH = 200
MEASUREMENT_CONTRACT_SIGNAL_DESCRIPTION_MAX_LENGTH = 1000


class ExpectedDirection(str, enum.Enum):
    """MVP-39 (frozen MVP-39B §N): an optional pre-execution expectation for
    one RequiredSignal. Stored as a plain ``String(20)`` guarded by a named
    CHECK — not a native PostgreSQL enum — mirroring ``ComparisonType``'s
    own precedent. Never reuses ``EvidenceRelationship.SUPPORTING``/
    ``CONTRADICTING`` (``app/learning/models.py``): that vocabulary is
    confirmed post-hoc — it relates an already-recorded Signal to a
    Learning candidate, a different concept despite the superficial
    resemblance (MVP39B-OBS-3, preserved, not resolved here)."""

    INCREASE = "INCREASE"
    DECREASE = "DECREASE"
    TARGET = "TARGET"
    NO_DIRECTION = "NO_DIRECTION"


class MeasurementContractVersion(Base, UUIDPrimaryKeyMixin):
    """MVP-39 (frozen MVP-39A/-39B): one immutable, append-only version of
    an Experiment's PRE-EXECUTION measurement intent, bound to ONE immutable
    ``ExperimentDefinitionVersion`` — the same physical shape as
    ``ExperimentDefinitionVersion`` itself, one level down. The logical
    "contract" is the Experiment's own slot; this table holds its N
    versions (``version`` starts at 1; the current tip is the highest
    ordinal). Its natural key is ``(experiment_id, version)``, not
    ``(definition_version_id, version)``: once the first version exists the
    Definition is permanently pinned (below), so ``definition_version_id``
    never changes across the whole series.

    MEASUREMENT CONTRACT != EVIDENCE != EVIDENCE BINDING != TRACKING
    IMPLEMENTATION != MEASUREMENT EXECUTION != ALLOCATION != EXPOSURE !=
    EXECUTION AUTHORIZATION != EXPERIMENT RESULT != WINNER != HYPOTHESIS
    VERDICT != LEARNING VALIDATION != ATTRIBUTION != CAUSALITY. Nothing
    here is an observed value, a score, a winner, a threshold/operator, or
    a link to evidence, tracking or commercial outcomes — no such column
    exists.

    Immutability is structural by absence: no ``updated_at``, no
    ``status``, no ``frozen_at``, no ``execution_authorized``, no
    ``created_by`` (the audit event carries the actor), no repository
    update/delete method. "At least one RequiredSignal" is a SERVICE-LEVEL
    invariant only (MVP39B-OBS-1) — no PostgreSQL CHECK can prove a
    cross-row minimum, the same class of gap as MVP37B-OBS-1/MVP38A-OBS-1.

    Definition pinning (MVP-39B §D/§17): the first Contract version pins
    the Definition immediately, exactly as ``ExperimentVariant`` does — both
    are equal-weight, independent siblings under the ONE shared
    ``ExperimentDefinitionService._has_pinning_children`` seam; neither
    blocks the other. Definition pin != Contract freeze (MVP-39B §E/§18):
    MVP-39 implements NO freeze mechanism at all — Contract revision stays
    legal indefinitely within this domain's own boundary (MVP39B-OBS-2), a
    future Execution Authorization domain owns any future Contract-pinning
    seam, exactly mirroring how Variant added itself to Definition's own
    seam rather than one being pre-built empty.

    Ownership: direct ``workspace_id`` plus a composite tenant- and
    Definition-safe FK against ``experiment_definition_versions(id,
    experiment_id, workspace_id)`` — the existing MVP-38 candidate key,
    reused, not duplicated."""

    __tablename__ = "measurement_contract_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["definition_version_id", "experiment_id", "workspace_id"],
            [
                "experiment_definition_versions.id",
                "experiment_definition_versions.experiment_id",
                "experiment_definition_versions.workspace_id",
            ],
            name="fk_measurement_contract_versions_definition_version_workspace",
        ),
        UniqueConstraint("experiment_id", "version", name="uq_measurement_contract_versions_experiment_version"),
        # MVP-39: candidate key purely so ``measurement_contract_signals``
        # can declare a composite tenant- and Contract-safe FK on
        # (contract_version_id, experiment_id, workspace_id) — the exact
        # same additive-candidate-key pattern MVP-38 already used for
        # ``experiment_variants``.
        UniqueConstraint(
            "id", "experiment_id", "workspace_id", name="uq_measurement_contract_versions_id_experiment_workspace"
        ),
        UniqueConstraint(
            "workspace_id",
            "client_request_id",
            name="uq_measurement_contract_versions_workspace_client_request_id",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "measurement_window_days IS NULL OR measurement_window_days > 0", name="window_days_positive"
        ),
        # All five Contract-level prose fields are OPTIONAL (frozen
        # MVP-39B §U), but never blank-string when present — the same
        # "required text nonblank" discipline extended to optional text.
        CheckConstraint(
            "(minimum_evidence IS NULL OR char_length(btrim(minimum_evidence)) > 0) AND "
            "(success_criterion IS NULL OR char_length(btrim(success_criterion)) > 0) AND "
            "(analysis_method_intent IS NULL OR char_length(btrim(analysis_method_intent)) > 0) AND "
            "(stopping_rule IS NULL OR char_length(btrim(stopping_rule)) > 0) AND "
            "(decision_rule_intent IS NULL OR char_length(btrim(decision_rule_intent)) > 0)",
            name="optional_text_nonblank",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    definition_version_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    version: Mapped[int] = mapped_column(Integer)
    # PRE-EXECUTION declarations only — none of these implies evidence
    # exists, is bound, or is sufficient (MVP-39B §AK). Duration only: not
    # anchored to Exposure, which does not exist (MVP-39B §Q).
    measurement_window_days: Mapped[int | None] = mapped_column(Integer, default=None)
    minimum_evidence: Mapped[str | None] = mapped_column(String(MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH), default=None)
    success_criterion: Mapped[str | None] = mapped_column(String(MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH), default=None)
    analysis_method_intent: Mapped[str | None] = mapped_column(
        String(MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH), default=None
    )
    stopping_rule: Mapped[str | None] = mapped_column(String(MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH), default=None)
    decision_rule_intent: Mapped[str | None] = mapped_column(
        String(MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH), default=None
    )
    client_request_id: Mapped[str] = mapped_column(String(EXPERIMENT_DEFINITION_CLIENT_REQUEST_ID_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MeasurementContractRequiredSignal(Base, UUIDPrimaryKeyMixin):
    """MVP-39 (frozen MVP-39B §I): a first-class, immutable child row of ONE
    ``MeasurementContractVersion`` — a declared metric/observation the
    Contract requires, with its own stable identity (a future evidence-
    binding, Result-governance, or Tracking-readiness FK target — none of
    which exists yet, MVP-39B §AK/§AL/§AN). NOT evidence: its mere
    existence never means evidence exists, is bound, or is sufficient.

    Immutable and append-only by absence: no ``updated_at``, no
    ``created_by``, no repository update/delete method, no correction. No
    ``observed_value``/``score``/threshold/operator field exists —
    ``success_criterion`` (Contract-level, bounded prose) is the only
    success-related field anywhere in this domain (MVP-39B §O).

    Parentage: composite FK ``(contract_version_id, experiment_id,
    workspace_id)`` to ``measurement_contract_versions`` — the database
    proves the Signal's Experiment and workspace equal the parent
    Contract's. ``ordinal`` is the 1-based declaration order within the
    Contract version, assigned under the Experiment row lock (mirrors
    ``ExperimentVariant.ordinal`` exactly). There is deliberately NO
    maximum number of RequiredSignals (MVP-39B §J: the row-based precedent
    already established by Variant/TrackingRequirement, not the JSONB-array
    precedent of ``controlled_factors``) — a minimum of one is a
    SERVICE-LEVEL invariant only (MVP39B-OBS-1).

    ``tracking_required`` is declarative only — never an FK, never a claim
    that tracking is implemented, validated, or that an event fired
    (MVP-39B §AL)."""

    __tablename__ = "measurement_contract_signals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["contract_version_id", "experiment_id", "workspace_id"],
            [
                "measurement_contract_versions.id",
                "measurement_contract_versions.experiment_id",
                "measurement_contract_versions.workspace_id",
            ],
            name="fk_measurement_contract_signals_contract_version_workspace",
        ),
        UniqueConstraint(
            "contract_version_id", "ordinal", name="uq_measurement_contract_signals_contract_version_ordinal"
        ),
        UniqueConstraint(
            "contract_version_id", "name", name="uq_measurement_contract_signals_contract_version_name"
        ),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint(
            "char_length(btrim(name)) > 0 AND char_length(btrim(description)) > 0", name="text_fields_nonblank"
        ),
        CheckConstraint(
            "expected_direction IS NULL OR expected_direction IN ('INCREASE', 'DECREASE', 'TARGET', 'NO_DIRECTION')",
            name="expected_direction_valid",
        ),
        CheckConstraint(
            "evidence_requirement IS NULL OR char_length(btrim(evidence_requirement)) > 0",
            name="evidence_nonblank_if_present",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    contract_version_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    ordinal: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(MEASUREMENT_CONTRACT_SIGNAL_NAME_MAX_LENGTH))
    description: Mapped[str] = mapped_column(String(MEASUREMENT_CONTRACT_SIGNAL_DESCRIPTION_MAX_LENGTH))
    expected_direction: Mapped[str | None] = mapped_column(String(20), default=None)
    evidence_requirement: Mapped[str | None] = mapped_column(
        String(MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH), default=None
    )
    tracking_required: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


EXECUTION_AUTHORIZATION_UNIT_OF_ASSIGNMENT_MAX_LENGTH = 200
EXECUTION_AUTHORIZATION_ALLOCATION_DESIGN_MAX_LENGTH = 2000
EXECUTION_AUTHORIZATION_REVOKED_REASON_MAX_LENGTH = 1000
EXECUTION_AUTHORIZATION_CLIENT_REQUEST_ID_MAX_LENGTH = 100


class ExecutionAuthorization(Base, UUIDPrimaryKeyMixin):
    """MVP-40 (frozen Execution Authorization Design Freeze): the immutable,
    append-only record that ONE specific, immutable configuration —
    (Experiment, the exact ``ExperimentDefinitionVersion`` tip, the complete
    ``ExperimentVariant`` set existing under that tip, the exact
    ``MeasurementContractVersion`` tip) — has been authorized to begin
    future execution.

    EXECUTION AUTHORIZATION != EXECUTION != ASSIGNMENT != EXPOSURE !=
    EVIDENCE BINDING != TRACKING VALIDATION != EXPERIMENT RESULT != WINNER
    != HYPOTHESIS VERDICT != EXPERIMENTAL VALIDITY != CAUSALITY. Nothing
    here writes any table other than ``execution_authorizations``,
    ``execution_authorization_variants`` and ``audit_events``, allocates a
    unit, publishes/distributes content, creates evidence, or links to
    Result/Winner/Learning/CommercialOutcome — no such column exists.

    Subject (frozen Design Freeze §B): pinned by immutable identity
    reference only, never a live/current join. ``unit_of_assignment``/
    ``allocation_design`` are the embedded Execution Configuration (frozen
    §D, model "EC1") — declared intent only, proving nothing about real
    assignment/allocation/delivery/exposure.

    Lifecycle (frozen §K, model "L4", mirrors ``CommercialObjective``/
    ``Offer`` exactly): no status enum. ``revoked_at``/``revoked_reason`` —
    both-null-or-both-set — derive "active" as ``revoked_at IS NULL``.
    ``superseded_by_execution_authorization_id`` is set only for the
    automatic-supersession case (frozen §M/§P). Revocation is one-shot and
    non-reversible; recovery is always a brand-new Authorization, never a
    reopened one — no Variant mutation exists anywhere (MVP38A-OBS-3
    preserved, not resolved by mutating Variant).

    Single active Authorization per Experiment (frozen §M): the partial
    unique index below is the actual DB backstop, never relied on as
    merely an application-level guard, mirroring
    ``uq_strategic_decisions_current_recommendation``'s own precedent. A
    new valid Authorization automatically, atomically supersedes the prior
    active one in the same transaction — never two committed active rows.

    Strategy (frozen §C, model "S1"): no FK, no persisted reference here.
    Current Strategy is a create-time gate only, re-derived from
    ``Hypothesis.strategy_id`` at write time, never stored — an
    Authorization remains historically valid even after its governing
    Strategy is later superseded.

    Contract freeze (frozen §O/§P, MVP39B-OBS-2 resolution): NOT a column
    on this table. ``ExperimentMeasurementContractService.declare_or_revise``
    itself refuses a Contract revision while an ACTIVE Authorization pins
    the current tip, and that refusal lifts automatically once no active
    Authorization remains — the freeze is enforced entirely by the OTHER
    writer, not by any field here. The ACTIVE-Authorization freeze reopens
    on revocation ONLY while no ``ExecutionStartAttestation`` exists for the
    Experiment (EXAUTH-DF-OBS-1, resolved by the Governed Execution Start
    design, model C1): once any Start exists the Contract lineage — and
    further Variant declaration — stay permanently frozen even after
    revocation. This table itself still carries no execution-start column;
    the Start is its own append-only row (``ExecutionStartAttestation``).

    Target/Content/Distribution/Tracking-implementation are all deliberately
    absent (frozen §T/§U/§V/§W/§X): ``tracking_required`` on a RequiredSignal
    remains declarative-only and is never mapped, verified, or bound here."""

    __tablename__ = "execution_authorizations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_id", "workspace_id"],
            ["experiments.id", "experiments.workspace_id"],
            name="fk_execution_authorizations_experiment_workspace",
        ),
        ForeignKeyConstraint(
            ["definition_version_id", "experiment_id", "workspace_id"],
            [
                "experiment_definition_versions.id",
                "experiment_definition_versions.experiment_id",
                "experiment_definition_versions.workspace_id",
            ],
            name="fk_execution_authorizations_definition_version_workspace",
        ),
        ForeignKeyConstraint(
            ["contract_version_id", "experiment_id", "workspace_id"],
            [
                "measurement_contract_versions.id",
                "measurement_contract_versions.experiment_id",
                "measurement_contract_versions.workspace_id",
            ],
            name="fk_execution_authorizations_contract_version_workspace",
        ),
        # Plain self-FK (frozen §AC): same-workspace/same-Experiment safety
        # for this specific reference is a WRITER invariant, not a DB
        # constraint — it can only ever be set by the single atomic
        # supersession transaction that already holds the Experiment lock
        # and already knows both rows share the same Experiment (see
        # ExecutionAuthorizationService._supersede). Explicit, shortened FK
        # name: the naming convention's own derived name exceeds
        # PostgreSQL's 63-character identifier limit.
        ForeignKeyConstraint(
            ["superseded_by_execution_authorization_id"],
            ["execution_authorizations.id"],
            name="fk_execution_authorizations_superseded_by_id",
        ),
        # MVP-40: candidate key purely so ``execution_authorization_variants``
        # can declare a composite tenant-safe FK on (authorization_id,
        # workspace_id).
        UniqueConstraint("id", "workspace_id", name="uq_execution_authorizations_id_workspace"),
        UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_execution_authorizations_workspace_client_request_id"
        ),
        # The actual DB backstop for "at most one ACTIVE Authorization per
        # Experiment" (frozen §16) — mirrors
        # uq_strategic_decisions_current_recommendation exactly.
        Index(
            "uq_execution_authorizations_experiment_active",
            "experiment_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        CheckConstraint(
            "(revoked_at IS NULL AND revoked_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_reason IS NOT NULL)",
            name="revocation_pairing",
        ),
        CheckConstraint(
            "char_length(btrim(unit_of_assignment)) > 0 AND char_length(btrim(allocation_design)) > 0",
            name="text_fields_nonblank",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    definition_version_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    contract_version_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    unit_of_assignment: Mapped[str] = mapped_column(String(EXECUTION_AUTHORIZATION_UNIT_OF_ASSIGNMENT_MAX_LENGTH))
    allocation_design: Mapped[str] = mapped_column(String(EXECUTION_AUTHORIZATION_ALLOCATION_DESIGN_MAX_LENGTH))
    client_request_id: Mapped[str] = mapped_column(String(EXECUTION_AUTHORIZATION_CLIENT_REQUEST_ID_MAX_LENGTH))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_reason: Mapped[str | None] = mapped_column(
        String(EXECUTION_AUTHORIZATION_REVOKED_REASON_MAX_LENGTH), default=None
    )
    superseded_by_execution_authorization_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExecutionAuthorizationVariant(Base, UUIDPrimaryKeyMixin):
    """MVP-40: one immutable snapshot row per ``ExperimentVariant`` included
    in an ``ExecutionAuthorization``'s Variant-set snapshot (frozen §G/§9).
    Pure association — deliberately no ``public_id`` (never an independent
    future FK target, mirrors ``ContentDistributionTrackingRequirement``'s
    own no-public-id precedent, NOT ``MeasurementContractRequiredSignal``'s).
    No ordinal/label duplication — the durable ``ExperimentVariant`` row
    itself remains the sole source of display data.

    Snapshot completeness (frozen §7/§9): every Variant existing under the
    pinned DefinitionVersion at Authorization-creation time is included —
    no selective/partial subset is possible through this writer. A Variant
    declared afterward is never retroactively added to an already-created
    Authorization's snapshot."""

    __tablename__ = "execution_authorization_variants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["authorization_id", "workspace_id"],
            ["execution_authorizations.id", "execution_authorizations.workspace_id"],
            name="fk_execution_authorization_variants_authorization_workspace",
        ),
        ForeignKeyConstraint(
            ["variant_id", "experiment_id", "workspace_id"],
            ["experiment_variants.id", "experiment_variants.experiment_id", "experiment_variants.workspace_id"],
            name="fk_execution_authorization_variants_variant_workspace",
        ),
        UniqueConstraint(
            "authorization_id", "variant_id", name="uq_execution_authorization_variants_authorization_variant"
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    authorization_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    experiment_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    variant_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


EXECUTION_START_CLIENT_REQUEST_ID_MAX_LENGTH = 100


class ExecutionStartAttestation(Base, UUIDPrimaryKeyMixin):
    """Governed Execution Start (frozen Governed Execution Start Design
    Freeze): the immutable, append-only record that an active workspace
    member ATTESTS that execution of ONE specific ``ExecutionAuthorization``
    began at an attested instant.

    EXECUTION START ATTESTATION != VERIFIED EXTERNAL EXECUTION !=
    ASSIGNMENT != DELIVERY != DISTRIBUTION != EXPOSURE != EVIDENCE !=
    MEASUREMENT != EXPERIMENT RESULT != WINNER != HYPOTHESIS VERDICT !=
    EXPERIMENTAL VALIDITY != CAUSALITY. The Core OS cannot observe external
    execution — this is human attestation only, never machine ingestion.

    ``started_at`` = the operator-attested execution-start instant
    (required, timezone-aware, NO server default). ``created_at`` = the
    server record time (``server_default=func.now()``). Both are stored and
    never conflated: ``started_at`` is the future window-anchor candidate,
    ``created_at`` is record chronology. ``started_at >=
    authorization.created_at`` and ``started_at <= now + 5 minutes`` are
    SERVICE-LEVEL invariants only (cross-row / clock-relative — no DB CHECK
    can express them, EXPROV-DISC-OBS-6).

    Cardinality ``ExecutionAuthorization 1 : 0..1 ExecutionStartAttestation``
    is DB-enforced by ``UNIQUE(authorization_id)``. Tenant safety is the
    composite FK to the existing ``(id, workspace_id)`` candidate key. No
    speculative candidate key is declared on this table (no concrete inbound
    FK targets it yet — same discipline as ``TrackingPlan``).

    Immutable and append-only by absence: no ``updated_at``, no
    ``created_by`` (the audit event carries the actor), no ``status``, no
    ``ended_at``, no repository update/delete method. Experiment, Definition,
    Contract, Variant snapshot and Campaign are all DERIVED through the
    Authorization — never denormalized here. Any Start for an Experiment
    permanently freezes its Contract lineage and further Variant declaration
    (EXAUTH-DF-OBS-1 resolution); recovery is a new Experiment lineage."""

    __tablename__ = "execution_start_attestations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["authorization_id", "workspace_id"],
            ["execution_authorizations.id", "execution_authorizations.workspace_id"],
            name="fk_execution_start_attestations_authorization_workspace",
        ),
        UniqueConstraint("authorization_id", name="uq_execution_start_attestations_authorization_id"),
        UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_execution_start_attestations_workspace_client_request_id"
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    authorization_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    client_request_id: Mapped[str] = mapped_column(String(EXECUTION_START_CLIENT_REQUEST_ID_MAX_LENGTH))
