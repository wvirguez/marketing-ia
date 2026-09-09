"""Organization / Workspace / Membership.

Tenancy chain per BACKEND-01: ``User -> Membership -> Organization ->
Workspace``. Preserved invariants:

- ORGANIZATION OWNERSHIP != WORKSPACE AUTHORIZATION: owning the parent
  Organization row is not itself an authorization check anywhere in this
  codebase — every access decision goes through Membership.
- Authorization decisions are made from a `Membership` row the caller
  actually has, never from a client-supplied `workspace_id` alone (see
  ``app/workspaces/service.py``).
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

_AI_PREFERENCE_FIELD_MAX_LENGTH = 32

# BACKEND-12 Governance Freeze §J/§10 (repair): the complete, frozen
# allow-list of notification keys — verified against the current
# frontend (`apps/web/lib/settings-demo-data.ts`'s `initialNotifications`,
# consistently used everywhere the frontend reads notification ids) and
# translated to snake_case machine keys. No key beyond these five is
# accepted anywhere in this module; extending this list is a governance
# decision, not something an unknown-key PATCH silently grows.
NOTIFICATION_KEYS: tuple[str, ...] = (
    "campaign_ready",
    "content_review",
    "metrics_available",
    "analysis_complete",
    "weekly_summary",
)

# GOVERNANCE PRODUCT DEFAULT (BACKEND-12 Freeze §K) — not canonical
# BACKEND-01 data. Sourced from the current frontend's own default
# (`components/settings/notifications-section.tsx` seeds every toggle to
# `true`). Applied only as an application-projection default when no
# stored override exists for a given key — never written to a row's
# ``toggles`` document merely because it was projected.
NOTIFICATION_DEFAULTS: dict[str, bool] = dict.fromkeys(NOTIFICATION_KEYS, True)


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))


class Workspace(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "workspaces"

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)


class MembershipRole(str, enum.Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"


class MembershipStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class Membership(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (
        # "one active membership per user/workspace" — a partial unique
        # index (Postgres-specific, fine since this app targets Postgres
        # only) so a REVOKED historical row never blocks re-inviting the
        # same user to the same workspace later.
        Index(
            "uq_memberships_active_user_workspace",
            "user_id",
            "workspace_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    role: Mapped[MembershipRole] = mapped_column(Enum(MembershipRole, name="membership_role", native_enum=True))
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, name="membership_status", native_enum=True),
        default=MembershipStatus.ACTIVE,
        server_default=MembershipStatus.ACTIVE.value,
    )


class AIPreference(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """BACKEND-12 Governance Freeze §F/§L: "Per-workspace tone/depth/
    creativity ... settings" (BACKEND-01 domain-model catalog) — one row
    per workspace. Owned/written here by ``workspaces``; read-only,
    never written, by ``orchestration``/``agents`` once either exists
    (neither does yet — BACKEND-12 §Z: no FK from orchestration to this
    table is added). No "approval settings" field exists anywhere on
    this model — that phrase has no defined canonical meaning and is
    deliberately never modeled (Freeze §G/§M): PREFERENCE CONFIGURATION
    != GOVERNANCE AUTHORITY. No native enum for tone/depth/creativity —
    no canonical vocabulary was ever named; each is a bounded,
    machine-safe string validated at the API boundary, not a frozen
    value set. No ``public_id`` — reached only via
    ``/api/v1/workspaces/{id}/settings``, already addressed by the
    workspace's own public id.
    """

    __tablename__ = "ai_preferences"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), unique=True, index=True)
    tone: Mapped[str | None] = mapped_column(String(_AI_PREFERENCE_FIELD_MAX_LENGTH), default=None)
    depth: Mapped[str | None] = mapped_column(String(_AI_PREFERENCE_FIELD_MAX_LENGTH), default=None)
    creativity: Mapped[str | None] = mapped_column(String(_AI_PREFERENCE_FIELD_MAX_LENGTH), default=None)


class NotificationPreference(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """BACKEND-12 Governance Freeze §H/§N (repair §9-§10): "Per-user,
    per-workspace toggle set" (BACKEND-01) — exactly one row per
    ``(workspace_id, user_id)`` pair, never one row per notification
    type. ``toggles`` is a stored OVERRIDE map, not a full snapshot: an
    absent key means "use the product default" (see ``NOTIFICATION_DEFAULTS``
    in this module), applied only at the projection layer, never
    persisted merely because a GET returned it. Keys are restricted to
    ``NOTIFICATION_KEYS`` and values to booleans at the API/schema
    boundary (``app/workspaces/schemas.py``) — this column itself is
    plain ``JSONB``, not schema-enforced by Postgres, exactly because no
    canonical fixed enum of notification types exists to freeze into a
    stricter column type. No ``public_id`` — reached only via the same
    nested workspace settings route as ``AIPreference``.
    """

    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_notification_preferences_workspace_id_user_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    toggles: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
