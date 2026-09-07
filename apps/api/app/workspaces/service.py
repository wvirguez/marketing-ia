"""Tenant-isolation gate for workspace access.

No route in BACKEND-04 accepts an arbitrary client-supplied
``workspace_id`` — the only workspace a request can act on is the one
derived from the caller's own authenticated session/membership (see
``app/auth/dependencies.py::get_current_workspace``). This service
exists anyway, ready for future by-ID endpoints, and is exercised
directly by the multi-tenancy tests: it is the pattern every future
"look up a workspace by its public id" endpoint must use.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.api_errors import ForbiddenError
from app.workspaces.models import Workspace
from app.workspaces.repository import MembershipRepository, WorkspaceRepository


class WorkspaceAccessService:
    def __init__(self, session: Session) -> None:
        self.workspaces = WorkspaceRepository(session)
        self.memberships = MembershipRepository(session)

    def get_authorized_workspace(self, *, user_id: uuid.UUID, workspace_public_id: str) -> Workspace:
        """Returns the workspace only if the given user has an active
        membership in it. Raises the exact same `ForbiddenError` whether
        the workspace does not exist at all or the user simply is not a
        member of it — the caller must never be able to distinguish
        "wrong ID" from "not yours" (see BACKEND-04 §22)."""
        workspace = self.workspaces.get_by_public_id(workspace_public_id)
        if workspace is None:
            raise ForbiddenError()

        membership = self.memberships.get_active_for_user_and_workspace(user_id, workspace.id)
        if membership is None:
            raise ForbiddenError()

        return workspace
