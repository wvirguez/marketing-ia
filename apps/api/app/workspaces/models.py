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

from sqlalchemy import Enum, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


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
