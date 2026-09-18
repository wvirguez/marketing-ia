"""Orchestration foundation — BACKEND-06.

Persists a business-stage workflow structure for a ``CampaignRun``
(``RunStageExecution``) and the human-in-the-loop scaffolding
(``HumanDecisionRequest``/``HumanDecisionResponse``, named exactly as in
`docs/backend/BACKEND-01-ARCHITECTURE.md` §4).

ORCHESTRATION FOUNDATION != AI EXECUTION: nothing in this module ever
calls an AI provider, invokes an agent, or produces a real specialist
output. A ``RunStageExecution`` only ever tracks *that a stage exists in
this run's workflow and what lifecycle state it is in* — never a
generated payload. See ``app/orchestration/service.py`` for the
transition rules that are the actual guarantee behind that sentence.

Relationship to BACKEND-01's "Campaign Phase" (domain-model doc, §1,
Campaign entity table): that entity is explicitly modeled as a
*derived, display-only* value ("which one phase is currently active"),
deliberately not its own stored table, to avoid a second source of
truth once it can be computed from Agent Run + Agent Definition data
(BACKEND-07+). ``RunStageExecution`` is not that field — no Agent Run
exists yet to derive anything from. It is new ground: the persisted
*workflow structure* itself (which stages exist, in what order, in what
state), which a future "current phase" projection can read from without
creating a second, competing fact — reading the currently non-terminal
stage from this table *is* the derivation, not a duplicate of it.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, ForeignKeyConstraint, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BusinessStage(str, enum.Enum):
    """Public, business-language stage vocabulary (BACKEND-06 §7) — never
    an internal ``AGENT-0N`` identifier. Ordered per
    ``BUSINESS_STAGE_ORDER`` below, matching the module boundary already
    proposed in `docs/backend/BACKEND-01-ARCHITECTURE.md` §9
    (research/strategy/planning/content/assets/distribution/paid_media/
    tracking/measurement/learning) and the `ProgressProjector` mapping
    in §3 of that document (AGENT-01=research, AGENT-02=audience,
    AGENT-03=strategy, AGENT-04=plan, AGENT-05=content/creative,
    AGENT-06=distribution, AGENT-09=paid media, AGENT-10=tracking;
    measurement/learning follow the same module list)."""

    RESEARCH = "RESEARCH"
    AUDIENCE = "AUDIENCE"
    STRATEGY = "STRATEGY"
    PLAN = "PLAN"
    CONTENT = "CONTENT"
    CREATIVE = "CREATIVE"
    DISTRIBUTION = "DISTRIBUTION"
    PAID_MEDIA = "PAID_MEDIA"
    TRACKING = "TRACKING"
    MEASUREMENT = "MEASUREMENT"
    LEARNING = "LEARNING"


BUSINESS_STAGE_ORDER: tuple[BusinessStage, ...] = (
    BusinessStage.RESEARCH,
    BusinessStage.AUDIENCE,
    BusinessStage.STRATEGY,
    BusinessStage.PLAN,
    BusinessStage.CONTENT,
    BusinessStage.CREATIVE,
    BusinessStage.DISTRIBUTION,
    BusinessStage.PAID_MEDIA,
    BusinessStage.TRACKING,
    BusinessStage.MEASUREMENT,
    BusinessStage.LEARNING,
)


class StageExecutionStatus(str, enum.Enum):
    """BACKEND-06 §11. Terminal states (``COMPLETED``/``FAILED``/
    ``SKIPPED``/``CANCELLED``) have no outgoing edges in the transition
    matrix (``app/orchestration/transitions.py``) — enforced there, not
    just documented here."""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class RunStageExecution(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "run_stage_executions"
    __table_args__ = (
        Index("uq_run_stage_executions_run_stage", "campaign_run_id", "stage", unique=True),
        Index("uq_run_stage_executions_run_ordinal", "campaign_run_id", "ordinal", unique=True),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    # Direct, not "via campaign_run" — matches the CampaignRun precedent
    # (BACKEND-05) of tenant-owned rows carrying their own workspace_id
    # rather than only being reachable by traversal. Always derived from
    # the parent CampaignRun at creation time, never an independent
    # input (see app/orchestration/repository.py).
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_runs.id"), index=True)
    stage: Mapped[BusinessStage] = mapped_column(Enum(BusinessStage, name="business_stage", native_enum=True))
    # Immutable, persisted ordering — never derived from insertion order.
    ordinal: Mapped[int] = mapped_column()
    status: Mapped[StageExecutionStatus] = mapped_column(
        Enum(StageExecutionStatus, name="stage_execution_status", native_enum=True),
        default=StageExecutionStatus.PENDING,
        server_default=StageExecutionStatus.PENDING.value,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Short, human-readable context only — never a payload, never a
    # generated output (BACKEND-06 §10/§27).
    blocked_reason: Mapped[str | None] = mapped_column(String(500), default=None)
    failure_reason: Mapped[str | None] = mapped_column(String(500), default=None)


class DecisionRequestStatus(str, enum.Enum):
    """Exact BACKEND-01 terminology (`docs/backend/BACKEND-01-ARCHITECTURE.md`
    §4): ``OPEN → RESOLVED | EXPIRED | CANCELLED``. BACKEND-06 never
    itself transitions a request to ``EXPIRED`` — that policy is
    explicitly deferred by BACKEND-01 itself ("policy TBD in a later
    stage"); the value exists for schema completeness only."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class HumanDecisionRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """BACKEND-01 domain-model doc: "Human Decision Request... belongs to
    Orchestration Run", tenant = Workspace (direct). No direct
    ``campaign_id`` column — deliberately, matching that exact
    relationship; a response's campaign is always reached by joining
    through its ``campaign_run``, never duplicated."""

    __tablename__ = "human_decision_requests"

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_runs.id"), index=True)
    stage_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("run_stage_executions.id"), default=None, index=True
    )
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[DecisionRequestStatus] = mapped_column(
        Enum(DecisionRequestStatus, name="decision_request_status", native_enum=True),
        default=DecisionRequestStatus.OPEN,
        server_default=DecisionRequestStatus.OPEN.value,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class HumanDecisionResponse(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One response per request in this stage (BACKEND-06 §17 MVP
    choice, consistent with BACKEND-01 §4: "RESOLVED requires exactly
    one Human Decision Response") — enforced by the unique index below,
    not only by the service-layer status check."""

    __tablename__ = "human_decision_responses"
    __table_args__ = (
        Index("uq_human_decision_responses_request", "decision_request_id", unique=True),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    decision_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("human_decision_requests.id"), index=True)
    responded_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    response_text: Mapped[str] = mapped_column(Text)


class StrategicDecisionType(str, enum.Enum):
    """Exact MVP-28A/-R1/-R2 frozen vocabulary — no fourth value. A
    StrategicDecision may only be recorded against a Recommendation whose
    own ``decision`` is ACCEPTED (enforced in
    ``app/orchestration/service.py``), so there is no ``REJECT`` type here:
    that would duplicate ``StrategicRecommendationDecision.REJECTED``
    rather than add new governance information (MVP-28A-R2 §J)."""

    ADOPT = "ADOPT"
    DEFER = "DEFER"
    DECLINE = "DECLINE"


class StrategicDecision(Base, UUIDPrimaryKeyMixin):
    """MVP-28A/-R1/-R2 frozen contract — a durable, higher-order governance
    record that a specific accepted ``StrategicRecommendationCandidate``'s
    proposed campaign-direction change has been ADOPTed, DEFERred, or
    DECLINEd. Never itself a Strategy mutation, a StrategicApproval, or an
    execution authorization (see ``app/orchestration/service.py``).

    STORAGE OWNERSHIP (MVP-28A-R2 §D, reversing MVP-28A-R1's own ``learning``
    freeze): this table lives in ``orchestration``, not ``learning`` or
    ``strategy`` — both of those modules' own docstrings explicitly disclaim
    Strategic Decision, and ``orchestration`` is the one bounded context
    positioned to absorb both this MVP's implemented origin (an accepted
    Learning Recommendation) and the documented, not-yet-implemented future
    origin (a Gate Decision) without asking either origin module to depend
    on the other.

    ORIGIN (Model C, frozen MVP-28A-R1 §E): ``strategic_recommendation_candidate_id``
    is nullable at the schema level, for the same reason
    ``StrategicRecommendationCandidate.strategic_implication_id`` is nullable
    — to leave room for a future, separately-authorized alternate origin
    (a Gate Decision) without a schema change — but is REQUIRED by the
    service/API layer for every MVP-28B write (no GateDecision origin, no
    manual origin, no generic polymorphic origin implemented here).

    TENANCY: this FK is a plain, single-column reference, not a composite
    tenant-safe FK — ``strategic_recommendation_candidates`` deliberately
    has no ``UNIQUE(id, workspace_id)`` candidate key (BACKEND-14 Governance
    Freeze §30: "no speculative candidate key"), and adding one is out of
    this MVP's authorized scope (it would mean modifying ``learning``'s own
    frozen model). Tenant-safety is instead proven entirely at the service
    layer: the Recommendation is always resolved through
    ``StrategicRecommendationCandidateRepository.get_for_campaign_by_public_id``
    (already campaign-scoped, non-leaky) before its internal UUID is ever
    used to populate this column — a raw client-supplied UUID is never
    trusted (``app/orchestration/service.py::StrategicDecisionService``).

    IMMUTABILITY (MVP-28A-R2 §E/§J, Model II): DECISION CONTENT (every
    column except the two below) is immutable after insert — no generic
    update service, no PATCH, no DELETE. SUPERSESSION METADATA
    (``superseded_at``/``superseded_by_strategic_decision_id``) is the one
    permitted mutation, exactly once, only inside the dedicated governed
    ``supersede`` transaction (``StrategicDecisionService.supersede``) —
    never reset, reversed, or re-pointed. "Current" = ``superseded_at IS
    NULL``, never inferred from ``MAX(created_at)``.

    SUPERSESSION DIRECTION (MVP-28A-R2 §F, Model B): OLD -> NEW only — the
    original row carries the forward pointer, mirroring
    ``app/commercial/models.py``'s own ``CommercialObjective``/``Offer``
    disposition pattern exactly, reused here because it is a proven,
    already-tested mechanism for the same "at most one current, atomic
    supersession" invariant, not merely copied for convenience. No reverse
    ``supersedes_strategic_decision_id`` on the replacement (MVP-28A-R2 §F:
    "do not store both directions merely for convenience").

    CURRENT-DECISION INVARIANT (MVP-28A-R2 §H): at most one current
    StrategicDecision per StrategicRecommendationCandidate, enforced by the
    partial unique index below — the actual, unconditional DB-level
    backstop, never relied on as merely an application-level guard.
    """

    __tablename__ = "strategic_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_strategic_decisions_campaign_workspace",
        ),
        # Explicit, shortened FK name — the naming convention's own derived
        # name for this self-FK exceeds PostgreSQL's 63-character identifier
        # limit, the same repair BACKEND-14/MVP-27A-R1 already applied to
        # strategic_recommendation_candidate_id/superseded_by_offer_id.
        #
        # DEFERRABLE INITIALLY DEFERRED (unlike every other FK in this
        # codebase): the atomic supersession transaction
        # (StrategicDecisionService.supersede_decision) must set
        # ``original``'s two disposition columns together, in one UPDATE,
        # to satisfy ``disposition_complete`` below (a CHECK constraint
        # cannot itself be deferred in PostgreSQL) — which requires the
        # replacement's id before the replacement row exists yet, to keep
        # ``original`` correctly excluded from the partial unique index
        # below *before* the replacement is inserted (the partial index
        # cannot be made deferrable either, since PostgreSQL constraints
        # only support DEFERRABLE for FK/UNIQUE/PK/EXCLUDE, and a partial
        # UNIQUE INDEX is not itself a table constraint). Deferring only
        # this FK to end-of-transaction resolves the ordering conflict
        # without weakening either invariant: by commit time the
        # replacement row exists, so the reference is always valid.
        ForeignKeyConstraint(
            ["superseded_by_strategic_decision_id"],
            ["strategic_decisions.id"],
            name="fk_strategic_decisions_superseded_by_id",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "(superseded_at IS NULL AND superseded_by_strategic_decision_id IS NULL) OR "
            "(superseded_at IS NOT NULL AND superseded_by_strategic_decision_id IS NOT NULL)",
            name="disposition_complete",
        ),
        # MVP-28A-R2 §H/§14: the actual DB-level backstop for "at most one
        # current Decision per Recommendation" — never relied on as merely
        # an application-level guard. NULL strategic_recommendation_candidate_id
        # rows (none exist yet, reserved for a future alternate origin) are
        # not constrained by this index (Postgres partial-unique semantics:
        # a NULL indexed column never participates in the uniqueness check).
        Index(
            "uq_strategic_decisions_current_recommendation",
            "strategic_recommendation_candidate_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    # Plain FK, deliberately not composite — see class docstring "TENANCY".
    strategic_recommendation_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategic_recommendation_candidates.id"), default=None, index=True
    )
    decision_type: Mapped[StrategicDecisionType] = mapped_column(
        Enum(StrategicDecisionType, name="strategic_decision_type", native_enum=True)
    )
    # The governance judgment/context and the proposed campaign-direction
    # change it addresses — one narrative field, the same "canonical
    # silence beyond that" precedent already used throughout the learning/
    # commercial chains (MVP-28A-R2 §I: this is where the "Strategic
    # Decision Subject" — distinct from the origin artifact above — is
    # actually recorded; no separate structured Strategy linkage exists).
    statement: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    superseded_by_strategic_decision_id: Mapped[uuid.UUID | None] = mapped_column(default=None)


class StrategicApprovalOutcome(str, enum.Enum):
    """MVP-29A/-29B frozen vocabulary — exactly two terminal outcomes, no
    third member. No ``PENDING``/``REQUESTED``/``UNDER_REVIEW``/``DEFERRED``
    is ever added here: a StrategicApproval row is only ever created once
    the governance ruling has already been made (MVP-29A §G, the same
    "NULL means no decision yet, never a member of the enum" rule
    ``StrategicRecommendationDecision`` itself already establishes)."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class StrategicApproval(Base, UUIDPrimaryKeyMixin):
    """MVP-29A frozen contract, implemented by MVP-29B — a durable,
    strictly insert-only governance record that one specific, currently
    ADOPT-typed StrategicDecision has separately received (or been denied)
    the ratification required to become eligible for a future, not-yet-
    implemented StrategyRevision workflow. Recording an Approval is never
    itself a Strategy mutation and never creates a StrategyRevision — see
    ``app/orchestration/service.py::StrategicApprovalService``.

    STORAGE OWNERSHIP: ``orchestration``, for the identical reason
    ``StrategicDecision`` lives here (MVP-28A-R2 §D) — this is the one
    bounded context positioned to hold both halves of the
    Decision/Approval pair without asking ``learning``/``strategy`` to
    depend on each other.

    LIFECYCLE / CARDINALITY (MVP-29A §G/§J, frozen): one-shot terminal
    ruling, no reopening, no supersession of its own — StrategicDecision
    1 -> 0..1 StrategicApproval. Reconsideration is never represented by
    mutating or replacing an Approval row; it flows entirely through
    ``StrategicDecisionService.supersede_decision`` (already-built,
    already-audited) followed by an independent Approval against the new
    Decision, if any (MVP-29A §J/§O — "Approval non-inheritance").

    IMMUTABILITY: every column is set exactly once at INSERT — no
    generic update service, no PATCH, no DELETE, no ``updated_at``.
    Stricter than ``StrategicDecision`` itself (which permits exactly one
    later write to its own supersession metadata): an Approval has no
    supersession metadata of its own to write.

    ELIGIBILITY (enforced in the service layer, not by a DB constraint
    that cannot express a sibling table's own column values, MVP-29A §F/
    §T): an Approval may be recorded only against a StrategicDecision
    whose own ``decision_type`` is ``ADOPT`` and whose own
    ``superseded_at`` is still NULL at commit time.

    TENANCY: ``strategic_decision_id`` is a plain, single-column FK, not a
    composite tenant-safe FK — ``strategic_decisions`` has no
    ``UNIQUE(id, workspace_id)`` candidate key, and adding one is out of
    this MVP's authorized scope (MVP-29A §P). Tenant-safety is instead
    proven entirely at the service layer: the Decision is always resolved
    through ``StrategicDecisionRepository.get_for_campaign_by_public_id``
    (already campaign-scoped, non-leaky) before its internal UUID is ever
    used to populate this column.

    CARDINALITY BACKSTOP (MVP-29A §J, MVP-29B §11): the actual, unconditional
    DB-level backstop for "at most one Approval per Decision" is the plain
    (non-partial) ``UNIQUE`` constraint on ``strategic_decision_id`` below —
    never a partial index, since (unlike StrategicDecision) there is no
    supersession state on this row that would ever need excluding.
    """

    __tablename__ = "strategic_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_strategic_approvals_campaign_workspace",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    # Plain FK, deliberately not composite — see class docstring "TENANCY".
    # unique=True is the actual DB-level "at most one Approval per
    # Decision" backstop (see class docstring "CARDINALITY BACKSTOP").
    strategic_decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategic_decisions.id"), unique=True, index=True
    )
    outcome: Mapped[StrategicApprovalOutcome] = mapped_column(
        Enum(StrategicApprovalOutcome, name="strategic_approval_outcome", native_enum=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
