"""Tenant-isolation gate for workspace access, plus (BACKEND-12) the
Settings persistence service for AIPreference/NotificationPreference and
the Workspace profile section.

No route in BACKEND-04 accepts an arbitrary client-supplied
``workspace_id`` — the only workspace a request can act on is the one
derived from the caller's own authenticated session/membership (see
``app/auth/dependencies.py::get_current_workspace``). ``WorkspaceAccessService``
exists anyway, ready for future by-ID endpoints, and is exercised
directly by the multi-tenancy tests: it is the pattern every future
"look up a workspace by its public id" endpoint must use.
BACKEND-12's ``/api/v1/workspaces/{id}/settings`` is the first such
by-public-id endpoint, using ``get_authorized_membership`` below.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.core.api_errors import ForbiddenError
from app.workspaces.models import AIPreference, Membership, NotificationPreference, Workspace
from app.workspaces.repository import (
    AIPreferenceRepository,
    MembershipRepository,
    NotificationPreferenceRepository,
    WorkspaceRepository,
)

# Sentinel distinguishing "field omitted from the PATCH" (leave
# unchanged) from "field explicitly present, possibly null" (BACKEND-12
# Governance Freeze §N) — a bare `None` default cannot carry this
# distinction, since `None` is also the valid "clear this override"
# value for AIPreference's tone/depth/creativity.
UNSET: Any = object()


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

    def get_authorized_membership(self, *, user_id: uuid.UUID, workspace_public_id: str) -> tuple[Workspace, Membership]:
        """Same non-leaky tenant gate as ``get_authorized_workspace``,
        additionally returning the caller's own ``Membership`` row for
        that workspace — BACKEND-12 needs the ``role`` to authorize the
        Admin-gated Settings PATCH sections (Freeze §Q)."""
        workspace = self.workspaces.get_by_public_id(workspace_public_id)
        if workspace is None:
            raise ForbiddenError()

        membership = self.memberships.get_active_for_user_and_workspace(user_id, workspace.id)
        if membership is None:
            raise ForbiddenError()

        return workspace, membership


class WorkspaceSettingsService:
    """BACKEND-12 Governance Freeze — Settings persistence for the
    Workspace profile, AIPreference, and NotificationPreference sections.

    A multi-section PATCH is ONE atomic command (Freeze Repair §H/§I):
    every submitted section's *authorization* is checked by the caller
    (``app/workspaces/router.py``) BEFORE this service is ever invoked —
    so an unauthorized request never reaches here at all, and this
    service either applies every requested mutation and commits exactly
    once, or raises before ``commit()`` and persists nothing.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.ai_preferences = AIPreferenceRepository(session)
        self.notification_preferences = NotificationPreferenceRepository(session)
        self.audit = AuditEventRepository(session)

    def get_settings(
        self, *, workspace: Workspace, user_id: uuid.UUID
    ) -> tuple[AIPreference | None, NotificationPreference | None]:
        """Read-only: never creates a row (BACKEND-12 Freeze §S/§17)."""
        ai_preference = self.ai_preferences.get_by_workspace_id(workspace.id)
        notification_preference = self.notification_preferences.get_by_workspace_and_user(workspace.id, user_id)
        return ai_preference, notification_preference

    def _get_or_create_ai_preference(self, *, workspace_id: uuid.UUID) -> AIPreference:
        existing = self.ai_preferences.get_by_workspace_id(workspace_id)
        if existing is not None:
            return existing
        try:
            with self.session.begin_nested():
                return self.ai_preferences.create(workspace_id=workspace_id)
        except IntegrityError:
            # A SAVEPOINT rollback (not a full transaction rollback) —
            # any other section's already-applied mutation earlier in
            # this same PATCH survives (BACKEND-12 Freeze Repair §23).
            existing = self.ai_preferences.get_by_workspace_id(workspace_id)
            if existing is not None:
                return existing
            raise

    def _get_or_create_notification_preference(
        self, *, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> NotificationPreference:
        existing = self.notification_preferences.get_by_workspace_and_user(workspace_id, user_id)
        if existing is not None:
            return existing
        try:
            with self.session.begin_nested():
                return self.notification_preferences.create(workspace_id=workspace_id, user_id=user_id)
        except IntegrityError:
            existing = self.notification_preferences.get_by_workspace_and_user(workspace_id, user_id)
            if existing is not None:
                return existing
            raise

    def patch_settings(
        self,
        *,
        workspace: Workspace,
        actor_user_id: uuid.UUID,
        workspace_name: Any = UNSET,
        tone: Any = UNSET,
        depth: Any = UNSET,
        creativity: Any = UNSET,
        notifications: dict[str, bool] | None = None,
    ) -> tuple[Workspace, AIPreference | None, NotificationPreference | None]:
        """Applies every requested section in ONE transaction. Singleton
        rows are created first (each race-safe via its own SAVEPOINT —
        see the two helpers above), before any field is actually
        mutated, so a uniqueness race resolved on one section can never
        unwind another section's already-applied change: nothing is
        mutated yet when those helpers run. Exactly one ``commit()``
        covers every mutation and every AuditEvent for this request."""
        workspace_changed = workspace_name is not UNSET
        ai_changed = tone is not UNSET or depth is not UNSET or creativity is not UNSET
        notifications_changed = bool(notifications)

        ai_preference: AIPreference | None = None
        notification_preference: NotificationPreference | None = None

        if ai_changed:
            ai_preference = self._get_or_create_ai_preference(workspace_id=workspace.id)
        if notifications_changed:
            notification_preference = self._get_or_create_notification_preference(
                workspace_id=workspace.id, user_id=actor_user_id
            )

        if workspace_changed:
            workspace.name = workspace_name
        if ai_preference is not None:
            if tone is not UNSET:
                ai_preference.tone = tone
            if depth is not UNSET:
                ai_preference.depth = depth
            if creativity is not UNSET:
                ai_preference.creativity = creativity
        if notification_preference is not None and notifications:
            merged = dict(notification_preference.toggles)
            merged.update(notifications)
            notification_preference.toggles = merged

        if workspace_changed:
            self.audit.record(
                workspace_id=workspace.id,
                event_type="workspace.profile.updated",
                actor_type=ActorType.USER,
                actor_user_id=actor_user_id,
            )
        if ai_changed:
            self.audit.record(
                workspace_id=workspace.id,
                event_type="workspace.ai_preferences.updated",
                actor_type=ActorType.USER,
                actor_user_id=actor_user_id,
            )
        if notifications_changed:
            self.audit.record(
                workspace_id=workspace.id,
                event_type="workspace.notification_preferences.updated",
                actor_type=ActorType.USER,
                actor_user_id=actor_user_id,
            )

        self.session.commit()
        return workspace, ai_preference, notification_preference
