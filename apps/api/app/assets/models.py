"""Assets bounded context — BACKEND-13.

Persists exactly the three entities the BACKEND-13 Governance Freeze
authorizes (Phase 1B-R + Phase 1D): ``CreativeBrief``, ``Asset``,
``AssetVersion``. Object storage, uploads, presigned URLs, CDN/filesystem
contracts, AI image/video generation, agent execution, Distribution,
CreativeBrief versioning, and any ``ContentVersion`` linkage are all
explicitly deferred — nothing below implements, references, or invents any
of them.

**Ownership (frozen):**
``ContentPiece -> CreativeBrief [0..1] -> Asset [1:N] -> AssetVersion
[1:N]``. No ``Asset.content_version_id``/``AssetVersion.content_version_id``
exists or is planned via this module — a future, immutable
``ContentVersion <-> AssetVersion`` association table remains DEFERRED
(Phase 1B-R, Model D/E analysis): a mutable "provenance" FK on a
logically-immutable-per-row entity was rejected as self-contradictory.

**CreativeBrief** is directly Workspace-tenant-owned, 0..1 per
``ContentPiece`` (``UniqueConstraint(content_piece_id)``), immutable, and
never replaced or versioned — no ``updated_at``, no ``status``, no
``archived_at``. It carries no ``public_id`` — it is never independently
addressable by a caller; every write path reaches it only through its
owning ``ContentPiece``.

**Asset** is directly Workspace-tenant-owned, 1:N per ``CreativeBrief``,
mutable only via ``archived_at`` (an independent soft-delete timestamp,
never a status value with its own transition edges — mirrors
``ContentPiece.archived_at``'s own precedent). ``kind`` is an open, bounded
string, not a native enum — BACKEND-01 names no closed vocabulary for it.
``status`` is a nullable, descriptive-only bounded string with no state
machine and no transition service.

**AssetVersion** is via-parent tenant-owned only (no own ``workspace_id``),
immutable, append-only — the same "is itself the version, no ordinal
column" shape ``ContentVersion`` already establishes. **Ordering (frozen,
Phase 1C):** current/latest is selected by ``ORDER BY created_at DESC, id
DESC`` using ``now()`` (the Postgres *transaction-start* timestamp, not
``clock_timestamp()``) — unlike ``ContentVersion``, no code path in this
module ever inserts two ``AssetVersion`` rows for the same Asset in one
transaction (§10), so ``now()``'s per-transaction granularity never makes
same-transaction ordering ambiguous; ``id DESC`` remains a deterministic
tie-breaker only, never claimed to be chronological.

PERSISTING AN ASSET != CONTENT APPROVED. ASSET EXISTS != READY FOR
DISTRIBUTION. ASSET EXISTS != DISTRIBUTED. ``Asset.status`` has no
authority over ``ContentPiece.status`` — nothing in this module mutates
``ContentPiece``, ``ContentApproval``, or any orchestration/measurement
table.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

_KIND_MAX_LENGTH = 100
_STATUS_MAX_LENGTH = 100


class CreativeBrief(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "creative_briefs"
    __table_args__ = (
        # 0..1 per ContentPiece (frozen, Phase 1B-R) — immutable,
        # non-replaceable. The database's own UNIQUE constraint is the
        # final race-safe protection (mirrors uq_content_briefs_plan_item_id).
        UniqueConstraint("content_piece_id", name="uq_creative_briefs_content_piece_id"),
        ForeignKeyConstraint(
            ["content_piece_id", "workspace_id"],
            ["content_pieces.id", "content_pieces.workspace_id"],
            name="fk_creative_briefs_content_piece_workspace",
        ),
        # BACKEND-13 (explicitly authorized, additive-only): a candidate
        # key purely so Asset can declare a composite, tenant-safe FK on
        # (creative_brief_id, workspace_id) — the same pattern already
        # established throughout BACKEND-08/09/10/11.
        UniqueConstraint("id", "workspace_id", name="uq_creative_briefs_id_workspace_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    content_piece_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    # No canonical field structure is named for Creative Brief beyond "a
    # brief" — a single opaque JSONB payload, distinct from ContentBrief's
    # own plain-text `brief` column since BACKEND-01 gives no worked
    # example to justify a narrower shape here.
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Asset(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "assets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["creative_brief_id", "workspace_id"],
            ["creative_briefs.id", "creative_briefs.workspace_id"],
            name="fk_assets_creative_brief_workspace",
        ),
        # No UNIQUE(id, workspace_id) here (Phase 2R §7): unlike
        # ContentPiece/CreativeBrief, nothing actually declares a
        # composite FK against assets(id, workspace_id) — AssetVersion
        # has no workspace_id column at all (via-parent tenancy only,
        # see below) and AuditEvent.asset_id is a plain single-column FK
        # to assets.id, matching every other AuditEvent subject-
        # attribution FK in this codebase. Governance Freeze preferred
        # omitting this candidate key unless a concrete FK required it;
        # none does, so it is not declared.
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    creative_brief_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    # Open, bounded string — no canonical kind vocabulary is named
    # anywhere, the same "do not invent an unnamed vocabulary" discipline
    # already applied to ContentPiece.format/Experiment.status.
    kind: Mapped[str] = mapped_column(String(_KIND_MAX_LENGTH))
    # Nullable, descriptive-only bounded string — no state machine, no
    # default lifecycle, no transition service exists for this column.
    status: Mapped[str | None] = mapped_column(String(_STATUS_MAX_LENGTH), default=None)
    # Independent of `status` — mirrors ContentPiece.archived_at's own
    # precedent exactly: a soft-delete timestamp, not itself a status
    # value with its own transition edges.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AssetVersion(Base, UUIDPrimaryKeyMixin):
    """Immutable, append-only — no update path anywhere in this module.
    No ``public_id`` (never independently addressable outside its owning
    Asset). No ``workspace_id`` (via-parent tenancy only, reached only
    through a workspace-scoped ``Asset``)."""

    __tablename__ = "asset_versions"

    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"), index=True)
    # Inert persistence only — no provider semantics, no parsing, no
    # validation, no dereferencing (§23).
    storage_reference: Mapped[str | None] = mapped_column(String(2048), default=None)
    # `metadata` is the frozen physical column name, but `metadata` is a
    # reserved attribute name on every SQLAlchemy declarative class (it
    # already holds the mapper's `MetaData` instance) — the Python
    # attribute is named `metadata_` to avoid that collision; the actual
    # database column is still named exactly `metadata`.
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Ordering (frozen, Phase 1C): `now()`, not `clock_timestamp()` — see
    # the module docstring. No command in this module ever inserts two
    # AssetVersion rows for the same Asset within one transaction (§10),
    # so `now()`'s per-transaction granularity never makes ordering
    # ambiguous in practice.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
