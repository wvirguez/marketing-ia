"""Content bounded context — BACKEND-10.

Persists exactly the four entities the BACKEND-10 Governance Freeze
authorizes (Phase 1 + Phase 1B, superseding the original Phase 1
recommendation where the two disagree): ``ContentBrief``, ``ContentPiece``,
``ContentVersion``, ``ContentApproval``. Content Revision Request, Creative
Brief, Asset, Asset Version, Distribution, Paid Media, Experiment/Variant
linkage, Strategy linkage, and any claim/evidence subsystem are all
explicitly deferred — nothing below implements, references, or invents any
of them. PLAN ITEM != CONTENT BRIEF. CONTENT BRIEF != CONTENT PIECE.
CONTENT PIECE != CONTENT VERSION. CONTENT VERSION != CONTENT APPROVAL.

**ContentBrief provenance (frozen, Phase 1B §C):** DERIVED VIA PLANNING
ANCESTRY. ``ContentBrief`` carries no ``campaign_id``, ``campaign_run_id``,
or ``stage_execution_id`` of its own — BACKEND-01's own text ("the creative
brief AGENT-04/03 hands to AGENT-05") makes AGENT-04/03 the producer and
AGENT-05 the consumer, meaning ``BusinessStage.CONTENT`` consumes the brief
rather than producing it. Provenance is fully recoverable by traversing
``plan_item_id -> content_plan_id -> campaign_id/campaign_run_id/
stage_execution_id`` on the existing ``ContentPlan`` row.

**ContentBrief tenancy (frozen, Phase 1B §E):** direct ``workspace_id`` (per
BACKEND-01's unqualified "Workspace" annotation), anchored for DB-level
tenant safety at ``ContentPlan`` — not at ``PlanItem``, which has no
``workspace_id`` column at all and must not gain one (BACKEND-09 remains
frozen). ``content_plan_id`` exists on this row *only* as a tenant-safety
anchor; it does not redefine ``ContentPlan`` as this entity's ownership
parent — ``PlanItem`` remains the sole semantic parent, and the service
layer (``app/content/service.py``) explicitly verifies
``plan_item.content_plan_id == content_plan_id`` at creation, the same
"a plain/composite FK alone cannot prove this" discipline
``StrategyService``/``ResearchService`` already apply to their own
provenance checks.

**Plan Item -> Content Brief cardinality (frozen, Phase 1B §G):** this is a
BACKEND-10 governance schema decision, not a BACKEND-01 mandate — BACKEND-01
itself is silent on the exact cardinality (only an ER-diagram inference
exists). ``UniqueConstraint(plan_item_id)`` is imposed here as the safer,
more-reversible engineering default; it must never be documented or read as
"BACKEND-01 requires 1:1." No re-brief mechanism (no ``supersedes_id``, no
``brief_version``, no ``replacement_brief_id``) exists.

**ContentPiece** is directly Workspace-tenant-owned (per BACKEND-01's
unqualified "Workspace" annotation), the stable, non-versioned logical
identity of one tracked unit of content, associated with exactly one
``ContentBrief`` (composite FK, tenant-safe). Its ``status`` uses the
*complete*, *canonical*, *named* state-machine-D vocabulary from
`docs/backend/BACKEND-01-ARCHITECTURE.md` §2D verbatim — unlike
``Experiment.status`` in BACKEND-08, BACKEND-01 explicitly names every
value here, so a native enum with the exact given values is used, not a
plain string. Enum *membership* is not the same as transition *authority*
— see ``app/content/transitions.py`` and ``app/content/service.py`` for
which edges this stage actually exposes a method for.

**ContentVersion** is via-parent tenant-owned only (``Workspace (via
Piece)``) — no own ``workspace_id``. Immutable, append-only, "is itself the
version" (BACKEND-01's own notation, shared only with Campaign Version,
distinct from Strategy/ResearchReport/ContentPlan's "Yes, with an explicit
version:int" notation). **No ordinal/version column exists here** (frozen,
Phase 1B §I): BACKEND-01's own explicit field list for this entity —
"Content Version (id, content_piece_id, created_at, created_by (agent or
user))" — omits one entirely; "current"/"latest" is derived deterministically
by ``ORDER BY created_at DESC, id DESC``, never by a stored ordinal.
``created_by_user_id`` is nullable, meaning system/agent origin, the same
convention ``AuditEvent.actor_user_id`` already establishes — no
``agent_id``/``agent_name``/``agent_public_id`` field exists or ever will
via this column.

**ContentApproval** is via-parent tenant-owned only (``Workspace (via
Version)``) — no own ``workspace_id``. Targets an exact ``ContentVersion``,
never ``ContentPiece`` directly. Uses the complete, canonical, named
state-machine-E vocabulary verbatim. "Immutable once resolved" — multiple
approval attempts, across the same or different versions, are all
preserved rows, never overwritten or deleted.

PERSISTING AN APPROVAL DECISION != HAVING AUTHORITY TO MAKE THAT DECISION.
AGENT-05 != final approver. AGENT-06 != content approver. WORKSPACE ROLE !=
CONTENT APPROVAL AUTHORITY — nothing below uses ``require_role`` or any
role/permission concept as a substitute for content-governance authority;
see ``app/content/service.py`` for the exact, narrow service boundary this
stage authorizes.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
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
_FUNNEL_STAGE_MAX_LENGTH = 100
_CTA_MAX_LENGTH = 500
_CHANNEL_MAX_LENGTH = 100
_OBJECTIVE_MAX_LENGTH = 1000


class ContentBrief(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "content_briefs"
    __table_args__ = (
        # Plan Item -> Content Brief cardinality: a BACKEND-10 governance
        # schema decision (Phase 1B §G), not a BACKEND-01 mandate. See the
        # module docstring above.
        UniqueConstraint("plan_item_id", name="uq_content_briefs_plan_item_id"),
        # Tenant safety, anchored at ContentPlan (Phase 1B §E) — not at
        # PlanItem, which has no workspace_id and must not gain one.
        ForeignKeyConstraint(
            ["content_plan_id", "workspace_id"],
            ["content_plans.id", "content_plans.workspace_id"],
            name="fk_content_briefs_content_plan_workspace",
        ),
        # BACKEND-10 (explicitly authorized, additive-only): a candidate
        # key purely so ContentPiece can declare a composite FK on
        # (content_brief_id, workspace_id), the same pattern
        # uq_strategies_id_workspace_id/uq_hypotheses_id_workspace_id
        # already established in BACKEND-08.
        UniqueConstraint("id", "workspace_id", name="uq_content_briefs_id_workspace_id"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    # Plain FK — PlanItem is the sole semantic parent; the composite FK
    # above (through content_plan_id) is the tenant-safety mechanism, not
    # a redefinition of ownership.
    plan_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plan_items.id"), index=True)
    content_plan_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    # BACKEND-01 names no itemized field structure for Content Brief (it
    # is conspicuously absent from the explicit field lists §5 gives for
    # every other Content entity) — a single narrative field, matching
    # Strategy.summary/ContentPlan.summary's own precedent.
    brief: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContentPieceStatus(str, enum.Enum):
    """Exact BACKEND-01 vocabulary, state machine D
    (`docs/backend/BACKEND-01-ARCHITECTURE.md` §2D) — no value renamed,
    none added. See ``app/content/transitions.py`` for which edges are
    legal, and ``app/content/service.py`` for which of those edges this
    stage actually exposes a method for (enum membership != transition
    authority)."""

    DRAFT = "DRAFT"
    IN_PRODUCTION = "IN_PRODUCTION"
    PRODUCED = "PRODUCED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    APPROVED = "APPROVED"
    READY_FOR_DISTRIBUTION = "READY_FOR_DISTRIBUTION"
    DISTRIBUTED = "DISTRIBUTED"
    ARCHIVED = "ARCHIVED"


class ContentPiece(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "content_pieces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["content_brief_id", "workspace_id"],
            ["content_briefs.id", "content_briefs.workspace_id"],
            name="fk_content_pieces_content_brief_workspace",
        ),
        # BACKEND-13 PREREQUISITE REPAIR (Phase 1D, explicitly authorized,
        # additive-only): a candidate key purely so CreativeBrief can
        # declare a composite, tenant-safe FK on (content_piece_id,
        # workspace_id) — the same pattern uq_content_briefs_id_workspace_id
        # already established for this exact table one level up. Logically
        # redundant (id is already the PK, so (id, workspace_id) can never
        # collide) but structurally required: PostgreSQL will not accept a
        # composite FK whose referenced columns have no matching UNIQUE
        # constraint or index, even when a subset of them is already a PK
        # (verified empirically in Phase 1D). No other ContentPiece
        # behavior changes.
        UniqueConstraint("id", "workspace_id", name="uq_content_pieces_id_workspace_id"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    content_brief_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    # Canonical Content Piece fields, `docs/backend/BACKEND-01-DOMAIN-MODEL.md`
    # §5: "Content Piece (id, campaign_id, format, status, objective,
    # funnel stage, cta, channel...)". No canonical vocabulary is named
    # for format/funnel_stage/cta/channel — plain bounded strings, not
    # native enums, the same "do not invent an unnamed vocabulary"
    # discipline already applied to Experiment.status in BACKEND-08.
    format: Mapped[str] = mapped_column(String(_FORMAT_MAX_LENGTH))
    objective: Mapped[str] = mapped_column(String(_OBJECTIVE_MAX_LENGTH))
    funnel_stage: Mapped[str] = mapped_column(String(_FUNNEL_STAGE_MAX_LENGTH))
    cta: Mapped[str] = mapped_column(String(_CTA_MAX_LENGTH))
    channel: Mapped[str] = mapped_column(String(_CHANNEL_MAX_LENGTH))
    status: Mapped[ContentPieceStatus] = mapped_column(
        Enum(ContentPieceStatus, name="content_piece_status", native_enum=True),
        default=ContentPieceStatus.DRAFT,
        server_default=ContentPieceStatus.DRAFT.value,
    )
    # Mirrors Campaign.archived_at's own precedent exactly: an
    # independent, nullable soft-delete timestamp, not itself a status
    # value with its own state-machine edges beyond what §2D names.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContentVersion(Base, UUIDPrimaryKeyMixin):
    """Belongs to exactly one ``ContentPiece`` — no direct ``workspace_id``
    column, tenant reached only by traversal through the parent Piece,
    matching BACKEND-01's own words: "Workspace (via Piece)" (the same
    "via parent" pattern already used by ``Positioning``/``ResearchSource``).
    Immutable, append-only, no update path anywhere in this module."""

    __tablename__ = "content_versions"

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    content_piece_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_pieces.id"), index=True)
    # Single JSONB payload for the heterogeneous, per-format body (kind:
    # "reel"|"carousel"|"story", mirroring apps/web/types/content-detail.ts's
    # own discriminated union almost exactly) — no per-format table, no
    # body/copy/script column on ContentPiece.
    payload: Mapped[dict] = mapped_column(JSON)
    # Nullable = system/agent origin, the same convention
    # AuditEvent.actor_user_id already establishes. No agent_id column
    # exists or is implied — no agent executes anything in this codebase.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    # `clock_timestamp()`, not `now()`: Postgres's `now()` returns the
    # *transaction* start time, identical for every row inserted within
    # one transaction — which would make "current = ORDER BY created_at
    # DESC" (the frozen, no-ordinal selection rule; see
    # app/content/repository.py::ContentVersionRepository.get_latest_for_piece)
    # non-deterministic for two Versions created in the same transaction.
    # `clock_timestamp()` is the real wall-clock time at each individual
    # statement's execution, guaranteeing every row gets a distinct,
    # monotonically increasing value even within one transaction. This
    # does not reintroduce an ordinal column — it only makes the already-
    # frozen timestamp-based ordering actually deterministic.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())


class ContentApprovalStatus(str, enum.Enum):
    """Exact BACKEND-01 vocabulary, state machine E
    (`docs/backend/BACKEND-01-ARCHITECTURE.md` §2E) — no value renamed,
    none added. `APPROVED_FOR_DISTRIBUTION`/`APPROVED_FOR_LIMITED_
    DISTRIBUTION`/`REVISION_REQUESTED` are NOT Content Approval statuses —
    the first two are not canonical anywhere, and the third is a
    ContentPiece state, not an Approval decision (Phase 1 §Q)."""

    REQUESTED = "REQUESTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ContentApproval(Base, UUIDPrimaryKeyMixin):
    """Belongs to exactly one ``ContentVersion`` — no direct
    ``workspace_id`` column, tenant reached only by traversal through the
    parent Version, matching BACKEND-01's own words: "Workspace (via
    Version)". Targets an exact Version, never a Piece directly. Multiple
    approval attempts (across the same or different Versions) are all
    preserved, immutable-once-resolved rows — never overwritten, never
    deleted (Phase 1B §V)."""

    __tablename__ = "content_approvals"

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    content_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_versions.id"), index=True)
    status: Mapped[ContentApprovalStatus] = mapped_column(
        Enum(ContentApprovalStatus, name="content_approval_status", native_enum=True),
        default=ContentApprovalStatus.REQUESTED,
        server_default=ContentApprovalStatus.REQUESTED.value,
    )
    # BACKEND-01 §5: "Content Approval (status, reviewer, decided_at)" —
    # matches this naming directly. Nullable until resolved.
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
