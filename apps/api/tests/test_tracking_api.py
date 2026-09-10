"""API contract, tenancy, and campaign-scope tests for the Tracking
surface (BACKEND-15 Governance Freeze §M/§N/§P). All marked `postgres`.

No public write endpoint exists for TrackingPlan/TrackingRequirement
creation, so test data is recorded via ``TrackingService`` against the
live app's own engine (the same pattern ``tests/test_learning_api.py``'s
helper uses), then read back / mutated through the real, authenticated
HTTP client.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as OrmSession

from app.campaigns.repository import CampaignRepository
from app.persistence.session import get_engine
from app.tracking.models import TrackingReadinessStatus
from app.tracking.service import TrackingService
from tests.campaignstest import campaign_payload, register_and_get_csrf

pytestmark = pytest.mark.postgres

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _record_plan_with_requirement(campaign_public_id: str) -> tuple[str, str]:
    """Records a TrackingPlan + one TrackingRequirement for the given
    Campaign via a genuinely separate, immediately-committed session
    bound to the app's own engine. Returns ``(plan_public_id,
    requirement_public_id)``."""
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(campaign_public_id)
        service = TrackingService(session)
        plan = service.record_tracking_plan(campaign=campaign)
        requirement = service.record_tracking_requirement(tracking_plan=plan, name="Purchase event")
        return plan.public_id, requirement.public_id


def _tracking_path(fixtures: dict) -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/tracking"


# --- route surface -------------------------------------------------------


def test_only_get_and_patch_routes_exist(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    path = _tracking_path(fixtures)
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].post(path, json={}).status_code == 405
    assert fixtures["client"].put(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405


# --- GET response shape --------------------------------------------------


def test_empty_tracking_response_for_campaign_with_no_plan(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].get(_tracking_path(fixtures))
    assert response.status_code == 200
    assert response.json() == {"plan": None}


def test_get_returns_frozen_shape(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])

    body = fixtures["client"].get(_tracking_path(fixtures)).json()
    assert set(body.keys()) == {"plan"}
    plan = body["plan"]
    assert plan["id"] == plan_id
    assert plan["status"] == "NOT_DEFINED"
    assert set(plan.keys()) == {"id", "status", "requirements"}
    assert len(plan["requirements"]) == 1
    requirement = plan["requirements"][0]
    assert requirement["id"] == requirement_id
    assert requirement["name"] == "Purchase event"
    assert requirement["status"] is None
    assert set(requirement.keys()) == {"id", "name", "status"}


def test_requirements_are_deterministically_ordered(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        service = TrackingService(session)
        plan = service.record_tracking_plan(campaign=campaign)
        first_id = service.record_tracking_requirement(tracking_plan=plan, name="First").public_id
        second_id = service.record_tracking_requirement(tracking_plan=plan, name="Second").public_id

    body = fixtures["client"].get(_tracking_path(fixtures)).json()
    ids = [r["id"] for r in body["plan"]["requirements"]]
    assert ids == [first_id, second_id]


# --- PATCH: TRANSITION_PLAN ------------------------------------------------


def test_patch_transitions_plan(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _record_plan_with_requirement(fixtures["campaign_id"])
    response = fixtures["client"].patch(
        _tracking_path(fixtures),
        json={"operation": "TRANSITION_PLAN", "target_status": "REQUIREMENTS_DEFINED"},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["plan"]["status"] == "REQUIREMENTS_DEFINED"


def test_patch_illegal_transition_returns_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _record_plan_with_requirement(fixtures["campaign_id"])
    response = fixtures["client"].patch(
        _tracking_path(fixtures),
        json={"operation": "TRANSITION_PLAN", "target_status": "CERTIFIED"},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"


def test_patch_transition_with_no_plan_is_forbidden(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].patch(
        _tracking_path(fixtures),
        json={"operation": "TRANSITION_PLAN", "target_status": "REQUIREMENTS_DEFINED"},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_patch_invalid_target_status_enum_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _record_plan_with_requirement(fixtures["campaign_id"])
    response = fixtures["client"].patch(
        _tracking_path(fixtures),
        json={"operation": "TRANSITION_PLAN", "target_status": "MAYBE"},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 422


# --- PATCH: UPDATE_REQUIREMENT_STATUS --------------------------------------


def test_patch_updates_requirement_status(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    response = fixtures["client"].patch(
        _tracking_path(fixtures),
        json={"operation": "UPDATE_REQUIREMENT_STATUS", "requirement_id": requirement_id, "status": "Configurado"},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 200, response.text
    requirement = response.json()["plan"]["requirements"][0]
    assert requirement["status"] == "Configurado"


def test_patch_can_clear_requirement_status_to_null(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _plan_id, requirement_id = _record_plan_with_requirement(fixtures["campaign_id"])
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    fixtures["client"].patch(
        _tracking_path(fixtures), json={"operation": "UPDATE_REQUIREMENT_STATUS", "requirement_id": requirement_id, "status": "Configurado"}, headers=headers,
    )
    response = fixtures["client"].patch(
        _tracking_path(fixtures), json={"operation": "UPDATE_REQUIREMENT_STATUS", "requirement_id": requirement_id, "status": None}, headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["plan"]["requirements"][0]["status"] is None


def test_patch_unknown_requirement_is_forbidden(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _record_plan_with_requirement(fixtures["campaign_id"])
    response = fixtures["client"].patch(
        _tracking_path(fixtures),
        json={"operation": "UPDATE_REQUIREMENT_STATUS", "requirement_id": "TRQ-TOTALLYFAKE0", "status": "x"},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_patch_requirement_status_on_certified_plan_is_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        service = TrackingService(session)
        plan = service.record_tracking_plan(campaign=campaign)
        requirement = service.record_tracking_requirement(tracking_plan=plan, name="Purchase event")
        from tests.contenttest import make_user

        reviewer = make_user(session)
        for target in (
            TrackingReadinessStatus.REQUIREMENTS_DEFINED,
            TrackingReadinessStatus.CONFIGURATION_PENDING,
            TrackingReadinessStatus.CONFIGURED,
            TrackingReadinessStatus.VERIFICATION_PENDING,
            TrackingReadinessStatus.CERTIFIED,
        ):
            service.transition_tracking_plan(campaign=campaign, target_status=target, actor_user_id=reviewer.id)
        requirement_id = requirement.public_id

    response = fixtures["client"].patch(
        _tracking_path(fixtures),
        json={"operation": "UPDATE_REQUIREMENT_STATUS", "requirement_id": requirement_id, "status": "x"},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TRACKING_REQUIREMENT_MUTATION_FORBIDDEN"


# --- strict DTO / discriminator -------------------------------------------


def test_patch_unknown_operation_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _record_plan_with_requirement(fixtures["campaign_id"])
    response = fixtures["client"].patch(
        _tracking_path(fixtures), json={"operation": "DELETE_EVERYTHING"}, headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 422


def test_patch_extra_field_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _record_plan_with_requirement(fixtures["campaign_id"])
    response = fixtures["client"].patch(
        _tracking_path(fixtures),
        json={"operation": "TRANSITION_PLAN", "target_status": "REQUIREMENTS_DEFINED", "name": "hijack"},
        headers={"X-CSRF-Token": fixtures["csrf_token"]},
    )
    assert response.status_code == 422


def test_patch_requires_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _record_plan_with_requirement(fixtures["campaign_id"])
    response = fixtures["client"].patch(
        _tracking_path(fixtures), json={"operation": "TRANSITION_PLAN", "target_status": "REQUIREMENTS_DEFINED"},
    )
    assert response.status_code == 403


# --- security --------------------------------------------------------------


def test_no_raw_uuid_or_forbidden_fields_in_response(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    _record_plan_with_requirement(fixtures["campaign_id"])
    text = fixtures["client"].get(_tracking_path(fixtures)).text
    assert not _UUID_RE.search(text), "tracking response leaked a raw UUID"
    lowered = text.lower()
    for forbidden in ("workspace_id", "tracking_status", "provider", "pixel_id", "agent_id"):
        assert forbidden not in lowered


def test_tracking_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/campaigns/CMP-FAKE00000000/tracking")
    assert response.status_code == 401


# --- tenancy / campaign-scope resource integrity -----------------------


def test_tenant_a_cannot_read_tenant_bs_tracking(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    _record_plan_with_requirement(body_b["campaign"]["id"])

    response = client_a.get(f"/api/v1/campaigns/{body_b['campaign']['id']}/tracking")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_wrong_workspace_patch_is_forbidden_and_does_not_mutate(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()
    _record_plan_with_requirement(body_b["campaign"]["id"])

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()
    response = client_a.patch(
        f"/api/v1/campaigns/{body_a['campaign']['id']}/tracking",
        json={"operation": "TRANSITION_PLAN", "target_status": "REQUIREMENTS_DEFINED"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_same_workspace_different_campaign_get_and_patch_are_isolated(campaign_run_client: dict) -> None:
    """MANDATORY two-campaign/same-workspace fixture (BACKEND-14/15 own
    precedent): Workspace W has Campaign A and Campaign B; Campaign B's
    Tracking artifacts must never appear in Campaign A's GET, and a PATCH
    through Campaign A's URL targeting Campaign B's requirement must be
    rejected exactly like a nonexistent requirement, with no mutation."""
    fixtures = campaign_run_client  # Campaign A
    plan_a_id, requirement_a_id = _record_plan_with_requirement(fixtures["campaign_id"])

    csrf = fixtures["csrf_token"]
    body_b = fixtures["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B"), headers={"X-CSRF-Token": csrf}
    ).json()
    campaign_b_id = body_b["campaign"]["id"]
    plan_b_id, requirement_b_id = _record_plan_with_requirement(campaign_b_id)

    # GET Campaign A must include only A's Plan/Requirements.
    body = fixtures["client"].get(_tracking_path(fixtures)).json()
    assert body["plan"]["id"] == plan_a_id
    assert [r["id"] for r in body["plan"]["requirements"]] == [requirement_a_id]

    # GET Campaign B must include only B's Plan/Requirements.
    body_b_get = fixtures["client"].get(f"/api/v1/campaigns/{campaign_b_id}/tracking").json()
    assert body_b_get["plan"]["id"] == plan_b_id
    assert [r["id"] for r in body_b_get["plan"]["requirements"]] == [requirement_b_id]

    # PATCH /campaigns/A/tracking targeting Requirement B must fail non-leakily, no mutation.
    response = fixtures["client"].patch(
        _tracking_path(fixtures),
        json={"operation": "UPDATE_REQUIREMENT_STATUS", "requirement_id": requirement_b_id, "status": "hijacked"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    # Confirm zero mutation: Requirement B is still untouched and can
    # still be updated correctly through its OWN campaign's URL.
    correct_response = fixtures["client"].patch(
        f"/api/v1/campaigns/{campaign_b_id}/tracking",
        json={"operation": "UPDATE_REQUIREMENT_STATUS", "requirement_id": requirement_b_id, "status": "Configurado"},
        headers={"X-CSRF-Token": csrf},
    )
    assert correct_response.status_code == 200
    assert correct_response.json()["plan"]["requirements"][0]["status"] == "Configurado"
