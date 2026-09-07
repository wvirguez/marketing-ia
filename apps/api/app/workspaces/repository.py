"""Data access for Organization / Workspace / Membership.

No repository here calls ``session.commit()`` — see
``app/persistence/session.py``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import generate_public_id
from app.workspaces.models import Membership, MembershipRole, MembershipStatus, Organization, Workspace
from app.workspaces.slugs import generate_workspace_slug


class OrganizationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, name: str) -> Organization:
        organization = Organization(public_id=generate_public_id("ORG"), name=name)
        self.session.add(organization)
        self.session.flush()
        return organization


class WorkspaceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, organization_id: uuid.UUID, name: str) -> Workspace:
        workspace = Workspace(
            public_id=generate_public_id("WKS"),
            organization_id=organization_id,
            name=name,
            slug=generate_workspace_slug(name),
        )
        self.session.add(workspace)
        self.session.flush()
        return workspace

    def get_by_id(self, workspace_id: uuid.UUID) -> Workspace | None:
        return self.session.get(Workspace, workspace_id)

    def get_by_public_id(self, public_id: str) -> Workspace | None:
        return self.session.execute(select(Workspace).where(Workspace.public_id == public_id)).scalar_one_or_none()


class MembershipRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, user_id: uuid.UUID, workspace_id: uuid.UUID, role: MembershipRole) -> Membership:
        membership = Membership(
            public_id=generate_public_id("MBR"),
            user_id=user_id,
            workspace_id=workspace_id,
            role=role,
            status=MembershipStatus.ACTIVE,
        )
        self.session.add(membership)
        self.session.flush()
        return membership

    def get_active_for_user_and_workspace(self, user_id: uuid.UUID, workspace_id: uuid.UUID) -> Membership | None:
        return self.session.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.workspace_id == workspace_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
        ).scalar_one_or_none()

    def get_first_active_for_user(self, user_id: uuid.UUID) -> Membership | None:
        """MVP heuristic for "current workspace": a freshly-registered
        user has exactly one membership, so the earliest-created active
        one is unambiguous. A real workspace switcher (multiple
        memberships, an explicit "current workspace" selection) is a
        later, separately authorized concern — the schema already
        supports multiple memberships per user; only this convenience
        lookup is single-workspace-shaped."""
        return self.session.execute(
            select(Membership)
            .where(Membership.user_id == user_id, Membership.status == MembershipStatus.ACTIVE)
            .order_by(Membership.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()
