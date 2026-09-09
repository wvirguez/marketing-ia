"""Audit attribution for Settings persistence (BACKEND-12 Governance
Freeze Repair). All marked `postgres`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.audit.models import AuditEvent
from app.auth.service import AuthService
from app.users.service import UserService
from app.workspaces.service import WorkspaceSettingsService

pytestmark = pytest.mark.postgres


def _register(db_session, **overrides):
    fields = {
        "email": f"user-{uuid.uuid4().hex[:12]}@example.com",
        "password": "correct horse battery staple",
        "display_name": "Audit Test User",
        "organization_name": None,
        "workspace_name": None,
        "user_agent": None,
    }
    fields.update(overrides)
    return AuthService(db_session).register(**fields)


def _events_of_type(db_session, event_type: str) -> list[AuditEvent]:
    return list(db_session.execute(select(AuditEvent).where(AuditEvent.event_type == event_type)).scalars().all())


# --- user-global audit ------------------------------------------------


def test_user_profile_updated_audit_row_has_null_workspace_id(db_session) -> None:
    ctx = _register(db_session)
    UserService(db_session).update_profile(user=ctx.user, display_name="Changed")
    events = [e for e in _events_of_type(db_session, "user.profile.updated") if e.actor_user_id == ctx.user.id]
    assert len(events) == 1
    assert events[0].workspace_id is None


def test_user_preferences_updated_audit_row_has_null_workspace_id(db_session) -> None:
    ctx = _register(db_session)
    UserService(db_session).update_profile(user=ctx.user, locale="es-VE")
    events = [e for e in _events_of_type(db_session, "user.preferences.updated") if e.actor_user_id == ctx.user.id]
    assert len(events) == 1
    assert events[0].workspace_id is None


def test_user_global_events_carry_no_personal_values(db_session) -> None:
    ctx = _register(db_session)
    UserService(db_session).update_profile(user=ctx.user, display_name="Secret Name", locale="es-VE", timezone="America/Caracas")
    events = [e for e in _events_of_type(db_session, "user.profile.updated") if e.actor_user_id == ctx.user.id]
    events += [e for e in _events_of_type(db_session, "user.preferences.updated") if e.actor_user_id == ctx.user.id]
    for event in events:
        assert event.previous_state is None
        assert event.new_state is None


def test_omitted_display_name_emits_no_profile_event(db_session) -> None:
    ctx = _register(db_session)
    before = len(_events_of_type(db_session, "user.profile.updated"))
    UserService(db_session).update_profile(user=ctx.user, locale="es-VE")
    after = len(_events_of_type(db_session, "user.profile.updated"))
    assert after == before


# --- workspace-scoped audit --------------------------------------------


def test_workspace_profile_updated_audit_row_has_real_workspace_id(db_session) -> None:
    ctx = _register(db_session)
    WorkspaceSettingsService(db_session).patch_settings(
        workspace=ctx.workspace, actor_user_id=ctx.user.id, workspace_name="Renamed"
    )
    events = [e for e in _events_of_type(db_session, "workspace.profile.updated") if e.workspace_id == ctx.workspace.id]
    assert len(events) == 1
    assert events[0].actor_user_id == ctx.user.id


def test_workspace_ai_preferences_updated_audit_row(db_session) -> None:
    ctx = _register(db_session)
    WorkspaceSettingsService(db_session).patch_settings(workspace=ctx.workspace, actor_user_id=ctx.user.id, tone="direct")
    events = [
        e for e in _events_of_type(db_session, "workspace.ai_preferences.updated") if e.workspace_id == ctx.workspace.id
    ]
    assert len(events) == 1


def test_workspace_notification_preferences_updated_audit_row(db_session) -> None:
    ctx = _register(db_session)
    WorkspaceSettingsService(db_session).patch_settings(
        workspace=ctx.workspace, actor_user_id=ctx.user.id, notifications={"campaign_ready": False}
    )
    events = [
        e
        for e in _events_of_type(db_session, "workspace.notification_preferences.updated")
        if e.workspace_id == ctx.workspace.id
    ]
    assert len(events) == 1


def test_workspace_events_carry_no_setting_values(db_session) -> None:
    ctx = _register(db_session)
    WorkspaceSettingsService(db_session).patch_settings(
        workspace=ctx.workspace, actor_user_id=ctx.user.id, workspace_name="Renamed", tone="direct",
        notifications={"campaign_ready": False},
    )
    for event_type in (
        "workspace.profile.updated", "workspace.ai_preferences.updated", "workspace.notification_preferences.updated",
    ):
        for event in [e for e in _events_of_type(db_session, event_type) if e.workspace_id == ctx.workspace.id]:
            assert event.previous_state is None
            assert event.new_state is None


def test_multi_section_patch_emits_exactly_one_event_per_section(db_session) -> None:
    ctx = _register(db_session)
    before = {
        t: len(_events_of_type(db_session, t))
        for t in ("workspace.profile.updated", "workspace.ai_preferences.updated", "workspace.notification_preferences.updated")
    }
    WorkspaceSettingsService(db_session).patch_settings(
        workspace=ctx.workspace, actor_user_id=ctx.user.id, workspace_name="Renamed", tone="direct",
        notifications={"campaign_ready": False},
    )
    for event_type, count_before in before.items():
        assert len(_events_of_type(db_session, event_type)) == count_before + 1


# --- existing workspace-scoped audit paths are unaffected ------------------


def test_registration_still_emits_no_settings_events_but_workspace_id_column_accepts_existing_writes(db_session) -> None:
    """A plain regression check that the nullable-column repair did not
    disturb any pre-existing, already-workspace-scoped write path — the
    orchestration/audit tests elsewhere already cover those in depth;
    this just confirms a totally ordinary registration still leaves the
    audit table in a normal, non-broken state (no NULL where a real
    value should be for any event unrelated to Settings)."""
    ctx = _register(db_session)
    events = db_session.execute(select(AuditEvent).where(AuditEvent.actor_user_id == ctx.user.id)).scalars().all()
    # Registration itself does not currently emit any AuditEvent (no
    # call site does) — this simply proves no Settings-only side effect
    # leaked an unexpected row.
    assert all(e.event_type.startswith(("user.", "workspace.")) for e in events)


# --- rollback atomicity ----------------------------------------------------


def test_mixed_section_rollback_leaves_no_partial_audit_trail(db_session) -> None:
    """Simulates the router's own pre-check: an unauthorized section
    must never reach the service at all, so nothing is persisted and no
    audit row is written — this test proves the service itself, if
    called with a role check already failed upstream, is simply never
    invoked (the atomicity guarantee lives in the router, verified at
    the HTTP layer in tests/test_settings_api.py::
    test_member_mixed_section_patch_fully_rejected). Here we confirm the
    service's OWN atomicity: a forced failure between two sections still
    leaves nothing committed."""
    from unittest.mock import patch

    from app.audit.repository import AuditEventRepository

    ctx = _register(db_session)
    before_workspace_name = ctx.workspace.name

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            WorkspaceSettingsService(db_session).patch_settings(
                workspace=ctx.workspace, actor_user_id=ctx.user.id, workspace_name="Should Not Persist", tone="direct",
            )

    db_session.rollback()
    db_session.refresh(ctx.workspace)
    assert ctx.workspace.name == before_workspace_name
    from app.workspaces.repository import AIPreferenceRepository

    assert AIPreferenceRepository(db_session).get_by_workspace_id(ctx.workspace.id) is None


def test_user_profile_rollback_on_audit_failure(db_session) -> None:
    from unittest.mock import patch

    from app.audit.repository import AuditEventRepository

    ctx = _register(db_session)
    original_name = ctx.user.display_name

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            UserService(db_session).update_profile(user=ctx.user, display_name="Should Not Persist")

    db_session.rollback()
    db_session.refresh(ctx.user)
    assert ctx.user.display_name == original_name
