from __future__ import annotations

from pydantic import BaseModel

from app.workspaces.models import Membership, Workspace


class WorkspacePublic(BaseModel):
    id: str
    name: str
    slug: str


class MembershipPublic(BaseModel):
    role: str


def workspace_to_public(workspace: Workspace) -> WorkspacePublic:
    return WorkspacePublic(id=workspace.public_id, name=workspace.name, slug=workspace.slug)


def membership_to_public(membership: Membership) -> MembershipPublic:
    return MembershipPublic(role=membership.role.value)
