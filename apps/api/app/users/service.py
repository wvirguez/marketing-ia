"""BACKEND-12 Governance Freeze — User profile + UserPreference
persistence (``PATCH /api/v1/users/me``).

USER-GLOBAL EVENTS ARE NOT WORKSPACE EVENTS: ``display_name`` and
``UserPreference`` (locale/timezone) have no workspace context at all
(BACKEND-01's own domain-model catalog: "User Preferences ... Belongs to
User", not tenant-owned) — the corresponding AuditEvent rows are written
with ``workspace_id=None`` (BACKEND-12 Governance Freeze Repair §C/§D),
which ``AuditEvent.workspace_id`` was made nullable specifically to
support, rather than fabricating a workspace for a workspace-independent
fact.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.users.models import User, UserPreference
from app.users.repository import UserPreferenceRepository, UserRepository

# Same sentinel convention as ``app/workspaces/service.py::UNSET`` —
# needed here too since ``None`` is the valid "clear this override"
# value for locale/timezone, distinct from "omitted, leave unchanged".
UNSET: Any = object()


class UserService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.preferences = UserPreferenceRepository(session)
        self.audit = AuditEventRepository(session)

    def _get_or_create_preference(self, *, user_id: uuid.UUID) -> UserPreference:
        existing = self.preferences.get_by_user_id(user_id)
        if existing is not None:
            return existing
        try:
            with self.session.begin_nested():
                return self.preferences.create(user_id=user_id)
        except IntegrityError:
            # SAVEPOINT rollback only — a `display_name` change applied
            # later in this same request is unaffected, since nothing is
            # mutated until after this singleton is confirmed to exist
            # (see `update_profile` below).
            existing = self.preferences.get_by_user_id(user_id)
            if existing is not None:
                return existing
            raise

    def update_profile(
        self,
        *,
        user: User,
        display_name: Any = UNSET,
        locale: Any = UNSET,
        timezone: Any = UNSET,
    ) -> tuple[User, UserPreference | None]:
        """One transaction covers the User update, the UserPreference
        upsert, and both AuditEvent rows — all persist together or none
        do (BACKEND-12 Freeze §20). A section (``display_name`` vs.
        ``preferences``) that was entirely omitted from the request never
        touches the database and never emits an event; a section that
        was present — even re-submitting the same value — is treated as
        a mutation for audit-emission purposes, matching this codebase's
        existing idempotent-write conventions elsewhere."""
        profile_changed = display_name is not UNSET
        preferences_changed = locale is not UNSET or timezone is not UNSET

        preference: UserPreference | None = None
        if preferences_changed:
            preference = self._get_or_create_preference(user_id=user.id)

        if profile_changed:
            user.display_name = display_name
        if preference is not None:
            if locale is not UNSET:
                preference.locale = locale
            if timezone is not UNSET:
                preference.timezone = timezone

        if profile_changed:
            self.audit.record(
                workspace_id=None,
                event_type="user.profile.updated",
                actor_type=ActorType.USER,
                actor_user_id=user.id,
            )
        if preferences_changed:
            self.audit.record(
                workspace_id=None,
                event_type="user.preferences.updated",
                actor_type=ActorType.USER,
                actor_user_id=user.id,
            )

        self.session.commit()
        return user, preference
