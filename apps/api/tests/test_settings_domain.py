"""Settings persistence — service-layer domain tests (BACKEND-12).
All marked `postgres`.

Uses `AuthService.register` directly against `db_session` to build a
User + Organization + Workspace + Membership(OWNER) without going
through HTTP — the same shape every prior stage's own domain test file
uses for its prerequisite fixtures.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.auth.service import AuthService
from app.core.api_errors import ForbiddenError
from app.users.repository import UserPreferenceRepository
from app.users.service import UNSET as USER_UNSET
from app.users.service import UserService
from app.workspaces.models import MembershipRole
from app.workspaces.repository import AIPreferenceRepository, MembershipRepository, NotificationPreferenceRepository
from app.workspaces.service import UNSET as WORKSPACE_UNSET
from app.workspaces.service import WorkspaceAccessService, WorkspaceSettingsService

pytestmark = pytest.mark.postgres


def _register(db_session, **overrides):
    fields = {
        "email": f"user-{uuid.uuid4().hex[:12]}@example.com",
        "password": "correct horse battery staple",
        "display_name": "Domain Test User",
        "organization_name": None,
        "workspace_name": None,
        "user_agent": None,
    }
    fields.update(overrides)
    return AuthService(db_session).register(**fields)


# --- UserPreference / User profile ------------------------------------


def test_get_me_style_lookup_with_no_preference_row_returns_none(db_session) -> None:
    ctx = _register(db_session)
    assert UserPreferenceRepository(db_session).get_by_user_id(ctx.user.id) is None


def test_patch_display_name_only_does_not_create_preference_row(db_session) -> None:
    ctx = _register(db_session)
    user, preference = UserService(db_session).update_profile(user=ctx.user, display_name="New Name")
    assert user.display_name == "New Name"
    assert preference is None
    assert UserPreferenceRepository(db_session).get_by_user_id(ctx.user.id) is None


def test_patch_preferences_lazily_creates_singleton_row(db_session) -> None:
    ctx = _register(db_session)
    _user, preference = UserService(db_session).update_profile(user=ctx.user, locale="es-VE")
    assert preference is not None
    assert preference.locale == "es-VE"
    assert preference.timezone is None
    assert UserPreferenceRepository(db_session).get_by_user_id(ctx.user.id).id == preference.id


def test_second_patch_updates_the_same_row_not_a_new_one(db_session) -> None:
    ctx = _register(db_session)
    service = UserService(db_session)
    _user, first = service.update_profile(user=ctx.user, locale="es-VE")
    _user, second = service.update_profile(user=ctx.user, timezone="America/Caracas")
    assert first.id == second.id
    assert second.locale == "es-VE"
    assert second.timezone == "America/Caracas"


def test_unique_constraint_on_user_id_is_enforced_at_db_level(db_session) -> None:
    ctx = _register(db_session)
    UserPreferenceRepository(db_session).create(user_id=ctx.user.id)
    db_session.flush()
    with pytest.raises(IntegrityError):
        UserPreferenceRepository(db_session).create(user_id=ctx.user.id)
        db_session.flush()
    db_session.rollback()


def test_partial_patch_preserves_omitted_preference_field(db_session) -> None:
    ctx = _register(db_session)
    service = UserService(db_session)
    service.update_profile(user=ctx.user, locale="es-VE", timezone="America/Caracas")
    _user, preference = service.update_profile(user=ctx.user, locale="en-US")
    assert preference.locale == "en-US"
    assert preference.timezone == "America/Caracas"


def test_explicit_null_clears_locale_and_timezone(db_session) -> None:
    ctx = _register(db_session)
    service = UserService(db_session)
    service.update_profile(user=ctx.user, locale="es-VE", timezone="America/Caracas")
    _user, preference = service.update_profile(user=ctx.user, locale=None, timezone=None)
    assert preference.locale is None
    assert preference.timezone is None


def test_omitted_preferences_argument_leaves_row_entirely_untouched(db_session) -> None:
    ctx = _register(db_session)
    service = UserService(db_session)
    service.update_profile(user=ctx.user, locale="es-VE")
    _user, preference = service.update_profile(user=ctx.user, display_name="Only Name Changed")
    assert preference is None  # not touched/returned — no preferences field was provided
    assert UserPreferenceRepository(db_session).get_by_user_id(ctx.user.id).locale == "es-VE"


def test_unset_sentinel_is_a_distinct_object_not_none(db_session) -> None:
    assert USER_UNSET is not None
    assert WORKSPACE_UNSET is not None
    assert USER_UNSET is not WORKSPACE_UNSET


# --- AIPreference -------------------------------------------------------


def test_ai_preference_lazily_created_on_first_patch(db_session) -> None:
    ctx = _register(db_session)
    workspace, ai_pref, notif_pref = WorkspaceSettingsService(db_session).patch_settings(
        workspace=ctx.workspace, actor_user_id=ctx.user.id, tone="direct"
    )
    assert ai_pref is not None
    assert ai_pref.tone == "direct"
    assert notif_pref is None
    assert AIPreferenceRepository(db_session).get_by_workspace_id(ctx.workspace.id).id == ai_pref.id


def test_ai_preference_unique_constraint_enforced(db_session) -> None:
    ctx = _register(db_session)
    AIPreferenceRepository(db_session).create(workspace_id=ctx.workspace.id)
    db_session.flush()
    with pytest.raises(IntegrityError):
        AIPreferenceRepository(db_session).create(workspace_id=ctx.workspace.id)
        db_session.flush()
    db_session.rollback()


def test_ai_preference_explicit_null_clears_override(db_session) -> None:
    ctx = _register(db_session)
    service = WorkspaceSettingsService(db_session)
    service.patch_settings(workspace=ctx.workspace, actor_user_id=ctx.user.id, tone="direct", depth="brief")
    _workspace, ai_pref, _notif = service.patch_settings(workspace=ctx.workspace, actor_user_id=ctx.user.id, tone=None)
    assert ai_pref.tone is None
    assert ai_pref.depth == "brief"


def test_get_settings_never_creates_a_row(db_session) -> None:
    ctx = _register(db_session)
    ai_pref, notif_pref = WorkspaceSettingsService(db_session).get_settings(workspace=ctx.workspace, user_id=ctx.user.id)
    assert ai_pref is None
    assert notif_pref is None
    assert AIPreferenceRepository(db_session).get_by_workspace_id(ctx.workspace.id) is None
    assert NotificationPreferenceRepository(db_session).get_by_workspace_and_user(ctx.workspace.id, ctx.user.id) is None


# --- NotificationPreference ----------------------------------------------


def test_notification_preference_lazily_created_with_only_submitted_overrides(db_session) -> None:
    ctx = _register(db_session)
    _workspace, _ai, notif = WorkspaceSettingsService(db_session).patch_settings(
        workspace=ctx.workspace, actor_user_id=ctx.user.id, notifications={"campaign_ready": False}
    )
    assert notif is not None
    assert notif.toggles == {"campaign_ready": False}


def test_notification_preference_unique_constraint_on_workspace_and_user(db_session) -> None:
    ctx = _register(db_session)
    NotificationPreferenceRepository(db_session).create(workspace_id=ctx.workspace.id, user_id=ctx.user.id)
    db_session.flush()
    with pytest.raises(IntegrityError):
        NotificationPreferenceRepository(db_session).create(workspace_id=ctx.workspace.id, user_id=ctx.user.id)
        db_session.flush()
    db_session.rollback()


def test_notification_patch_merges_keys_rather_than_replacing(db_session) -> None:
    ctx = _register(db_session)
    service = WorkspaceSettingsService(db_session)
    service.patch_settings(
        workspace=ctx.workspace, actor_user_id=ctx.user.id,
        notifications={"campaign_ready": False, "weekly_summary": False},
    )
    _workspace, _ai, notif = service.patch_settings(
        workspace=ctx.workspace, actor_user_id=ctx.user.id, notifications={"content_review": False}
    )
    assert notif.toggles == {"campaign_ready": False, "weekly_summary": False, "content_review": False}


def test_notification_preference_isolated_per_user_within_same_workspace(db_session) -> None:
    ctx = _register(db_session)
    other_ctx = _register(db_session)

    MembershipRepository(db_session).create(
        user_id=other_ctx.user.id, workspace_id=ctx.workspace.id, role=MembershipRole.MEMBER
    )
    service = WorkspaceSettingsService(db_session)
    service.patch_settings(workspace=ctx.workspace, actor_user_id=ctx.user.id, notifications={"campaign_ready": False})
    service.patch_settings(
        workspace=ctx.workspace, actor_user_id=other_ctx.user.id, notifications={"campaign_ready": True}
    )
    owner_pref = NotificationPreferenceRepository(db_session).get_by_workspace_and_user(ctx.workspace.id, ctx.user.id)
    member_pref = NotificationPreferenceRepository(db_session).get_by_workspace_and_user(
        ctx.workspace.id, other_ctx.user.id
    )
    assert owner_pref.toggles == {"campaign_ready": False}
    assert member_pref.toggles == {"campaign_ready": True}


# --- Tenancy / access gate ------------------------------------------------


def test_get_authorized_membership_rejects_non_member(db_session) -> None:
    ctx = _register(db_session)
    outsider_ctx = _register(db_session)
    with pytest.raises(ForbiddenError):
        WorkspaceAccessService(db_session).get_authorized_membership(
            user_id=outsider_ctx.user.id, workspace_public_id=ctx.workspace.public_id
        )


def test_get_authorized_membership_rejects_unknown_workspace_id(db_session) -> None:
    ctx = _register(db_session)
    with pytest.raises(ForbiddenError):
        WorkspaceAccessService(db_session).get_authorized_membership(
            user_id=ctx.user.id, workspace_public_id="WKS-TOTALLYFAKE0"
        )


def test_get_authorized_membership_returns_workspace_and_membership(db_session) -> None:
    ctx = _register(db_session)
    workspace, membership = WorkspaceAccessService(db_session).get_authorized_membership(
        user_id=ctx.user.id, workspace_public_id=ctx.workspace.public_id
    )
    assert workspace.id == ctx.workspace.id
    assert membership.id == ctx.membership.id
    assert membership.role == MembershipRole.OWNER


# --- Closed-domain non-mutation -------------------------------------------


def test_settings_writes_do_not_touch_membership_role(db_session) -> None:
    ctx = _register(db_session)
    WorkspaceSettingsService(db_session).patch_settings(
        workspace=ctx.workspace, actor_user_id=ctx.user.id, workspace_name="Renamed", tone="direct",
        notifications={"campaign_ready": False},
    )
    db_session.refresh(ctx.membership)
    assert ctx.membership.role == MembershipRole.OWNER
