"""API contract, tenancy, and authorization tests for the Settings API
(BACKEND-12). All marked `postgres`.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.workspaces.models import MembershipRole
from app.workspaces.repository import AIPreferenceRepository, WorkspaceRepository
from tests.campaignstest import register_and_get_csrf
from tests.settingstest import add_member_to_workspace, login_as

pytestmark = pytest.mark.postgres

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


# --- GET/PATCH /users/me --------------------------------------------------


def test_get_me_includes_preferences_with_nulls_when_absent(settings_client: dict) -> None:
    fixtures = settings_client
    body = fixtures["client"].get("/api/v1/users/me").json()
    assert body["preferences"] == {"locale": None, "timezone": None}


def test_patch_display_name_succeeds(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].patch(
        "/api/v1/users/me", json={"display_name": "Updated Name"}, headers={"X-CSRF-Token": fixtures["csrf_token"]}
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Updated Name"


def test_patch_preferences_lazy_creates_and_second_patch_updates(settings_client: dict) -> None:
    fixtures = settings_client
    csrf = {"X-CSRF-Token": fixtures["csrf_token"]}
    first = fixtures["client"].patch("/api/v1/users/me", json={"preferences": {"locale": "es-VE"}}, headers=csrf)
    assert first.json()["preferences"] == {"locale": "es-VE", "timezone": None}
    second = fixtures["client"].patch(
        "/api/v1/users/me", json={"preferences": {"timezone": "America/Caracas"}}, headers=csrf
    )
    assert second.json()["preferences"] == {"locale": "es-VE", "timezone": "America/Caracas"}


def test_explicit_null_clears_locale_and_timezone(settings_client: dict) -> None:
    fixtures = settings_client
    csrf = {"X-CSRF-Token": fixtures["csrf_token"]}
    fixtures["client"].patch(
        "/api/v1/users/me", json={"preferences": {"locale": "es-VE", "timezone": "America/Caracas"}}, headers=csrf
    )
    response = fixtures["client"].patch("/api/v1/users/me", json={"preferences": {"locale": None}}, headers=csrf)
    assert response.json()["preferences"] == {"locale": None, "timezone": "America/Caracas"}


def test_patch_me_requires_csrf(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].patch("/api/v1/users/me", json={"display_name": "No CSRF"})
    assert response.status_code == 403


def test_patch_me_rejects_email_field(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].patch(
        "/api/v1/users/me", json={"email": "new@example.com"}, headers={"X-CSRF-Token": fixtures["csrf_token"]}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("field,value", [("status", "DISABLED"), ("public_id", "USR-HACKED000000"), ("password_hash", "x")])
def test_patch_me_rejects_unsupported_fields(settings_client: dict, field: str, value: str) -> None:
    fixtures = settings_client
    response = fixtures["client"].patch(
        "/api/v1/users/me", json={field: value}, headers={"X-CSRF-Token": fixtures["csrf_token"]}
    )
    assert response.status_code == 422


def test_patch_me_rejects_null_display_name(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].patch(
        "/api/v1/users/me", json={"display_name": None}, headers={"X-CSRF-Token": fixtures["csrf_token"]}
    )
    assert response.status_code == 422


def test_users_me_requires_authentication(auth_client: TestClient) -> None:
    assert auth_client.get("/api/v1/users/me").status_code == 401
    assert auth_client.patch("/api/v1/users/me", json={"display_name": "x"}).status_code in (401, 403)


def test_no_raw_uuid_in_users_me_response(settings_client: dict) -> None:
    fixtures = settings_client
    text = fixtures["client"].get("/api/v1/users/me").text
    assert not _UUID_RE.search(text)


# --- GET /workspaces/{id}/settings ----------------------------------------


def test_get_settings_as_owner(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].get(f"/api/v1/workspaces/{fixtures['workspace_id']}/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["workspace"]["name"]
    assert body["ai_preferences"] == {"tone": None, "depth": None, "creativity": None}
    assert body["notifications"] == {
        "campaign_ready": True,
        "content_review": True,
        "metrics_available": True,
        "analysis_complete": True,
        "weekly_summary": True,
    }


def test_get_settings_as_admin(settings_client: dict) -> None:
    fixtures = settings_client
    admin = add_member_to_workspace(workspace_public_id=fixtures["workspace_id"], role=MembershipRole.ADMIN)
    admin_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    login_as(admin_client, email=admin["email"], password=admin["password"])
    response = admin_client.get(f"/api/v1/workspaces/{fixtures['workspace_id']}/settings")
    assert response.status_code == 200


def test_get_settings_as_member(settings_client: dict) -> None:
    fixtures = settings_client
    member = add_member_to_workspace(workspace_public_id=fixtures["workspace_id"], role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    login_as(member_client, email=member["email"], password=member["password"])
    response = member_client.get(f"/api/v1/workspaces/{fixtures['workspace_id']}/settings")
    assert response.status_code == 200


def test_get_settings_denied_for_non_member(settings_client: dict) -> None:
    fixtures = settings_client
    outsider = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    register_and_get_csrf(outsider, display_name="Outsider")
    response = outsider.get(f"/api/v1/workspaces/{fixtures['workspace_id']}/settings")
    assert response.status_code == 403


def test_get_settings_creates_no_ai_or_notification_row(settings_client: dict, db_session) -> None:
    fixtures = settings_client
    fixtures["client"].get(f"/api/v1/workspaces/{fixtures['workspace_id']}/settings")
    workspace = WorkspaceRepository(db_session).get_by_public_id(fixtures["workspace_id"])
    assert AIPreferenceRepository(db_session).get_by_workspace_id(workspace.id) is None


# --- PATCH /workspaces/{id}/settings — authorization ----------------------


def test_admin_or_owner_can_update_workspace_name(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"workspace": {"name": "Renamed Workspace"}},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 200
    assert response.json()["workspace"]["name"] == "Renamed Workspace"


def test_member_cannot_update_workspace_name(settings_client: dict) -> None:
    fixtures = settings_client
    member = add_member_to_workspace(workspace_public_id=fixtures["workspace_id"], role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    csrf = login_as(member_client, email=member["email"], password=member["password"])
    response = member_client.patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"workspace": {"name": "Hijacked"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403


def test_admin_or_owner_can_update_ai_preferences(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"ai_preferences": {"tone": "direct", "depth": "brief", "creativity": "conservative"}},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 200
    assert response.json()["ai_preferences"] == {"tone": "direct", "depth": "brief", "creativity": "conservative"}


def test_member_cannot_update_ai_preferences(settings_client: dict) -> None:
    fixtures = settings_client
    member = add_member_to_workspace(workspace_public_id=fixtures["workspace_id"], role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    csrf = login_as(member_client, email=member["email"], password=member["password"])
    response = member_client.patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"ai_preferences": {"tone": "direct"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403


def test_ai_preference_machine_safe_validation_rejects_bad_strings(settings_client: dict) -> None:
    fixtures = settings_client
    for bad in ("Profesional", "with space", "UPPER", "1startswithdigit", "<script>", "a" * 40):
        response = fixtures["client"].patch(
            f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
            json={"ai_preferences": {"tone": bad}},
            headers={"X-CSRF-Token": fixtures["csrf_token"]},
        )
        assert response.status_code == 422, bad


def test_ai_preference_explicit_null_clears_override(settings_client: dict) -> None:
    fixtures = settings_client
    csrf = {"X-CSRF-Token": fixtures["csrf_token"]}
    fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings", json={"ai_preferences": {"tone": "direct"}}, headers=csrf
    )
    response = fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings", json={"ai_preferences": {"tone": None}}, headers=csrf
    )
    assert response.json()["ai_preferences"]["tone"] is None


def test_member_can_update_own_notifications(settings_client: dict) -> None:
    fixtures = settings_client
    member = add_member_to_workspace(workspace_public_id=fixtures["workspace_id"], role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    csrf = login_as(member_client, email=member["email"], password=member["password"])
    response = member_client.patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"notifications": {"campaign_ready": False}},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["notifications"]["campaign_ready"] is False


def test_owner_can_update_own_notifications(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"notifications": {"weekly_summary": False}},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 200
    assert response.json()["notifications"]["weekly_summary"] is False


def test_another_users_notification_mutation_is_impossible(settings_client: dict) -> None:
    """There is no request parameter that names a target user_id at
    all — only the caller's own notifications are ever addressable."""
    fixtures = settings_client
    member = add_member_to_workspace(workspace_public_id=fixtures["workspace_id"], role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    csrf = login_as(member_client, email=member["email"], password=member["password"])
    member_client.patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"notifications": {"campaign_ready": False}},
        headers={"X-CSRF-Token": csrf},
    )
    # The owner's own settings remain the default — the member's write
    # could not have targeted the owner's row (no such parameter exists).
    owner_settings = fixtures["client"].get(f"/api/v1/workspaces/{fixtures['workspace_id']}/settings").json()
    assert owner_settings["notifications"]["campaign_ready"] is True


def test_notification_unknown_key_rejected(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"notifications": {"made_up_key": True}},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 422


def test_notification_null_value_rejected(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"notifications": {"campaign_ready": None}},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 422


def test_notification_non_boolean_value_rejected(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"notifications": {"campaign_ready": "yes"}},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 422


def test_notification_nested_object_rejected(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"notifications": {"campaign_ready": {"nested": True}}},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 422


def test_notification_partial_override_persisted_and_merged_on_get(settings_client: dict) -> None:
    fixtures = settings_client
    fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"notifications": {"campaign_ready": False}},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    body = fixtures["client"].get(f"/api/v1/workspaces/{fixtures['workspace_id']}/settings").json()
    assert body["notifications"] == {
        "campaign_ready": False,
        "content_review": True,
        "metrics_available": True,
        "analysis_complete": True,
        "weekly_summary": True,
    }


def test_notification_patch_merges_without_replacing(settings_client: dict) -> None:
    fixtures = settings_client
    csrf = {"X-CSRF-Token": fixtures["csrf_token"]}
    fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"notifications": {"campaign_ready": False, "weekly_summary": False}},
        headers=csrf,
    )
    response = fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"notifications": {"content_review": False}},
        headers=csrf,
    )
    body = response.json()["notifications"]
    assert body["campaign_ready"] is False
    assert body["weekly_summary"] is False
    assert body["content_review"] is False


# --- Mixed-section atomic authorization ------------------------------------


def test_member_mixed_section_patch_fully_rejected(settings_client: dict) -> None:
    fixtures = settings_client
    member = add_member_to_workspace(workspace_public_id=fixtures["workspace_id"], role=MembershipRole.MEMBER)
    member_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    csrf = login_as(member_client, email=member["email"], password=member["password"])
    response = member_client.patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"workspace": {"name": "Hijacked"}, "notifications": {"campaign_ready": False}},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403

    settings = fixtures["client"].get(f"/api/v1/workspaces/{fixtures['workspace_id']}/settings").json()
    assert settings["workspace"]["name"] != "Hijacked"
    assert settings["notifications"]["campaign_ready"] is True


def test_authorized_multi_section_patch_commits_all_sections(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={
            "workspace": {"name": "All Sections"},
            "ai_preferences": {"tone": "direct"},
            "notifications": {"campaign_ready": False},
        },
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workspace"]["name"] == "All Sections"
    assert body["ai_preferences"]["tone"] == "direct"
    assert body["notifications"]["campaign_ready"] is False


# --- Cross-workspace isolation / unknown ids -------------------------------


def test_member_of_one_workspace_cannot_read_another(settings_client: dict) -> None:
    fixtures = settings_client
    second_owner_client = TestClient(fixtures["client"].app, raise_server_exceptions=False)
    register_and_get_csrf(second_owner_client, display_name="Second Owner")
    other_workspace_id = second_owner_client.get("/api/v1/workspaces/current").json()["id"]

    response = fixtures["client"].get(f"/api/v1/workspaces/{other_workspace_id}/settings")
    assert response.status_code == 403


def test_unknown_workspace_id_is_indistinguishable_from_not_yours(settings_client: dict) -> None:
    fixtures = settings_client
    response = fixtures["client"].get("/api/v1/workspaces/WKS-TOTALLYFAKE0/settings")
    assert response.status_code == 403


def test_no_raw_uuid_in_settings_responses(settings_client: dict) -> None:
    fixtures = settings_client
    fixtures["client"].patch(
        f"/api/v1/workspaces/{fixtures['workspace_id']}/settings",
        json={"ai_preferences": {"tone": "direct"}, "notifications": {"campaign_ready": False}},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    text = fixtures["client"].get(f"/api/v1/workspaces/{fixtures['workspace_id']}/settings").text
    assert not _UUID_RE.search(text)
