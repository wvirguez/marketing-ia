"""Campaign / Campaign Brief / Campaign Run — BACKEND-05.

Tenant chain per BACKEND-01: a Campaign is directly Workspace-owned; a
Campaign Brief is tenant-scoped only transitively, through its Campaign
(no ``workspace_id`` column of its own — see the domain-model entity
catalog, ``Campaign Brief | ... | Workspace (via Campaign)``); a Campaign
Run carries its own direct ``workspace_id`` (domain-model catalog:
``Orchestration Run | ... | Workspace | belongs to Campaign``), matching
how ``Membership`` also carries direct foreign keys rather than deriving
tenancy purely by traversal.

Campaign ≠ Campaign Run (BACKEND-05 domain invariant): a Campaign is the
durable business entity; a Campaign Run is one orchestration attempt
against it. This module only *persists* a Campaign Run — creating one
here has no execution side effect of any kind (see
``app/campaigns/service.py``).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CampaignStatus(str, enum.Enum):
    """Mirrors state machine A (`docs/backend/BACKEND-01-ARCHITECTURE.md`
    §2A) in full, even though BACKEND-05 only ever persists ``SUBMITTED``
    (the state reachable at the end of an atomic create — brief captured,
    orchestration not yet started) — no code in this stage transitions a
    Campaign into any of the other listed states; those transitions
    belong to the orchestration runtime (BACKEND-06+)."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    ORCHESTRATING = "ORCHESTRATING"
    AWAITING_HUMAN_INPUT = "AWAITING_HUMAN_INPUT"
    READY_FOR_EXECUTION = "READY_FOR_EXECUTION"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"
    CANCELLED = "CANCELLED"


class CampaignRunStatus(str, enum.Enum):
    """Mirrors state machine B (`docs/backend/BACKEND-01-ARCHITECTURE.md`
    §2B). BACKEND-05 only ever persists ``CREATED`` — the run this stage
    creates is a persisted orchestration *request*, never executed, so no
    code here ever moves it to ``RUNNING``/``COMPLETED``/etc. Preserves
    PERSISTED RUN != EXECUTED ORCHESTRATION."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    AWAITING_HUMAN_DECISION = "AWAITING_HUMAN_DECISION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Campaign(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "campaigns"
    __table_args__ = (
        # A candidate key purely so `CampaignRun` can declare a composite
        # FK on (campaign_id, workspace_id) below — BACKEND-06 §6's
        # tenancy invariant ("CampaignRun.workspace_id MUST equal
        # Campaign.workspace_id") enforced at the database level, not
        # only in application code. `id` alone is already globally
        # unique, so this constraint adds no real-world ambiguity; it
        # exists only to give Postgres a matching unique target for the
        # composite foreign key.
        UniqueConstraint("id", "workspace_id", name="uq_campaigns_id_workspace_id"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, name="campaign_status", native_enum=True),
        default=CampaignStatus.SUBMITTED,
        server_default=CampaignStatus.SUBMITTED.value,
    )
    # Archival is deliberately modeled as an independent nullable
    # timestamp (the same "soft state" convention already used by
    # `AuthSession.revoked_at`), not as a `CampaignStatus.ARCHIVED`
    # transition — the state machine only reaches `ARCHIVED` via
    # `COMPLETED`, which no BACKEND-05 campaign ever does. A campaign can
    # be archived (hidden from default listings, preserved for
    # traceability) from any status without that implying it completed
    # an orchestration pipeline that does not exist yet.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class CampaignBrief(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "campaign_briefs"
    __table_args__ = (
        Index("uq_campaign_briefs_campaign_version", "campaign_id", "version", unique=True),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    version: Mapped[int] = mapped_column(default=1)
    # The original, user-authored prompt. Immutable once created — a
    # later re-run supersedes it with a new, higher-versioned Brief row;
    # this column is never updated in place (BACKEND-05 §7).
    prompt: Mapped[str] = mapped_column(Text)
    # Optional structured context, matching the frontend's
    # `CampaignContextFields` component and the domain model's
    # "Campaign Brief... captured context fields (product, price,
    # audience, budget, channel)" — plain nullable columns, not a JSON
    # blob, since the shape is small, fixed, and already known.
    product_type: Mapped[str | None] = mapped_column(String(100), default=None)
    price: Mapped[str | None] = mapped_column(String(60), default=None)
    audience: Mapped[str | None] = mapped_column(String(200), default=None)
    budget: Mapped[str | None] = mapped_column(String(60), default=None)
    channel: Mapped[str | None] = mapped_column(String(100), default=None)


class CampaignRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "campaign_runs"
    __table_args__ = (
        Index("uq_campaign_runs_campaign_run_number", "campaign_id", "run_number", unique=True),
        # BACKEND-06 §6 tenancy invariant, enforced at the database
        # layer: a CampaignRun's `workspace_id` must equal its parent
        # Campaign's `workspace_id`. A composite FK referencing
        # `campaigns (id, workspace_id)` makes it physically impossible
        # to insert/update a row where the two diverge — Postgres itself
        # rejects it, independent of and in addition to the
        # application-layer enforcement in
        # `app/campaigns/repository.py::CampaignRunRepository.create`
        # (which derives `workspace_id` from the `Campaign` object
        # rather than accepting it as an independent parameter at all).
        # `workspace_id` also keeps its own direct FK to `workspaces.id`
        # below (unrelated relationship, still required on its own).
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_campaign_runs_campaign_workspace",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)
    run_number: Mapped[int] = mapped_column()
    status: Mapped[CampaignRunStatus] = mapped_column(
        Enum(CampaignRunStatus, name="campaign_run_status", native_enum=True),
        default=CampaignRunStatus.CREATED,
        server_default=CampaignRunStatus.CREATED.value,
    )
