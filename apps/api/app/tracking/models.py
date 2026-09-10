"""Tracking bounded context — BACKEND-15.

Persists exactly the two entities the BACKEND-15 Governance Freeze (+
Freeze-R) authorizes: ``TrackingPlan``, ``TrackingRequirement``. "Tracking
Status" is deliberately **not** a table — it is the conceptual/read-model
name for ``TrackingPlan.status`` itself (Governance Freeze §D/TRK-D02/
TRK-D03), the same "one physical field, two names" precedent already
established for Campaign Run/Orchestration Run. Actual analytics/ad
platform config, OAuth, credentials, secret storage, webhooks, pixel
injection, and Tag Manager deployment are all explicitly deferred —
nothing below implements, references, or invents any of them.

**Ownership (frozen):** ``Campaign 1 -> 0..1 TrackingPlan`` via a direct
``campaign_id`` plus ``UNIQUE(campaign_id, workspace_id)`` (TRK-D01) — not
versioned, unlike Strategy/Content Plan, so no ``UniqueConstraint(campaign_id,
version)`` shape applies here. ``TrackingPlan 1 -> 0..N TrackingRequirement``
via a plain FK (``tracking_plan_id``), no composite/tenant-safe FK, since
``TrackingRequirement`` carries no ``workspace_id`` of its own (TRK-D19,
mirrors ``ContentApproval``'s own "Workspace (via Version)" convention: no
direct ``workspace_id``, tenancy proven only by loading the parent first).

**Tenancy:** ``TrackingPlan`` gets a direct, un-qualified ``workspace_id``
(domain-model: "Workspace") plus a composite tenant-safe FK to Campaign.
``TrackingPlan`` does NOT declare ``UNIQUE(id, workspace_id)`` (TRK-D20) —
no concrete FK in this domain targets it, since ``TrackingRequirement`` has
no ``workspace_id`` to pair into a composite key.

**Readiness source of truth (TRK-D02/TRK-D03):** ``TrackingPlan.status`` is
the *sole* physical readiness field — the 7-state graph in
``app/tracking/transitions.py``. No second status field, no derived/
persisted rollup, no automatic requirement-status-driven transition exists
anywhere (Governance Freeze §D, reaffirmed by Freeze-R §H).

**TrackingRequirement.status** (TRK-D08) is a plain, bounded, nullable
string — not a native enum — mirroring ``Experiment.status`` exactly
(``app/strategy/models.py``): BACKEND-01 names only "Mutable: Yes (status)"
without ever specifying a status vocabulary, so inventing a closed
enum/vocabulary here would fabricate semantics BACKEND-01 never specified.
It is purely descriptive/advisory and never automatically transitions
``TrackingPlan.status`` (TRK-D04/TRK-D28).

**Requirement mutation lifecycle (Freeze-R, TRK-D36-TRK-D41):**
``record_tracking_requirement`` is legal only while the parent Plan is in
``NOT_DEFINED``/``REQUIREMENTS_DEFINED``/``CONFIGURATION_PENDING``/
``FAILED_VERIFICATION``; ``update_tracking_requirement_status`` is legal in
every state except ``CERTIFIED``. Once ``CERTIFIED``, both the Requirement
set and every existing Requirement's status are frozen — a stable, terminal
attestation can never be undermined by a Requirement changing underneath it.

No ``campaign_id`` is duplicated on ``TrackingRequirement`` — Campaign
ancestry is derived by traversal (``TrackingRequirement -> TrackingPlan ->
Campaign``), never stored redundantly.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, UUIDPrimaryKeyMixin

_TRACKING_REQUIREMENT_NAME_MAX_LENGTH = 255
_TRACKING_REQUIREMENT_STATUS_MAX_LENGTH = 30


class TrackingReadinessStatus(str, enum.Enum):
    """Exact BACKEND-01 vocabulary, "G. Tracking Readiness" state machine
    (`docs/backend/BACKEND-01-ARCHITECTURE.md` §2G) — no value renamed,
    none added. See ``app/tracking/transitions.py`` for the exact legal
    edges."""

    NOT_DEFINED = "NOT_DEFINED"
    REQUIREMENTS_DEFINED = "REQUIREMENTS_DEFINED"
    CONFIGURATION_PENDING = "CONFIGURATION_PENDING"
    CONFIGURED = "CONFIGURED"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    FAILED_VERIFICATION = "FAILED_VERIFICATION"
    CERTIFIED = "CERTIFIED"


class TrackingPlan(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "tracking_plans"
    __table_args__ = (
        # Governance Freeze TRK-D01: Campaign 1 -> 0..1 TrackingPlan — a
        # governance choice (Versioned = No, singular canonical route),
        # not literal canonical cardinality text.
        UniqueConstraint("campaign_id", "workspace_id", name="uq_tracking_plans_campaign_workspace"),
        # Ownership: workspace-safe by construction — reuses the existing
        # `uq_campaigns_id_workspace_id` candidate key, no Campaign schema
        # change (TRK-D01/TRK-D19, mirrors app/strategy/models.py::Strategy).
        ForeignKeyConstraint(
            ["campaign_id", "workspace_id"],
            ["campaigns.id", "campaigns.workspace_id"],
            name="fk_tracking_plans_campaign_workspace",
        ),
        # No UNIQUE(id, workspace_id) here — no concrete FK in this domain
        # targets it (TRK-D20): TrackingRequirement carries no workspace_id
        # of its own to consume such a composite key.
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    status: Mapped[TrackingReadinessStatus] = mapped_column(
        Enum(TrackingReadinessStatus, name="tracking_readiness_status", native_enum=True),
        default=TrackingReadinessStatus.NOT_DEFINED,
        server_default=TrackingReadinessStatus.NOT_DEFINED.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrackingRequirement(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "tracking_requirements"
    # No composite/tenant-safe FK — TrackingRequirement carries no
    # workspace_id of its own (Workspace "(via Plan)" convention, mirrors
    # ContentApproval's own "(via Version)" — no direct workspace_id,
    # tenancy proven only by loading the parent TrackingPlan first).

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    tracking_plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tracking_plans.id"), index=True)
    # No canonical field structure beyond "one specific event/pixel/UTM
    # requirement" is named anywhere — a single open name/label field,
    # never a closed type/kind enum (TRK-D09/TRK-D25). "Event/pixel/UTM"
    # remain illustrative examples, not an exhaustive taxonomy.
    name: Mapped[str] = mapped_column(String(_TRACKING_REQUIREMENT_NAME_MAX_LENGTH))
    # Plain, bounded, nullable string — not a native enum — because
    # BACKEND-01 states only that Tracking Requirement "is mutable
    # (status)" without ever naming a status vocabulary; inventing one
    # here would fabricate semantics BACKEND-01 never specified
    # (TRK-D08, mirrors app/strategy/models.py::Experiment.status
    # exactly, including its own length precedent).
    status: Mapped[str | None] = mapped_column(String(_TRACKING_REQUIREMENT_STATUS_MAX_LENGTH), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
