"""API contract, tenancy, and campaign-scope tests for the Tracking
surface (BACKEND-15 Governance Freeze §M/§N/§P; creation added by
MVP-15B). All marked `postgres`.

Public POST routes now exist for TrackingPlan/TrackingRequirement
creation (MVP-15A/§15B) — see the "creation" sections below. Several
older tests still record fixture data directly via ``TrackingService``
against the live app's own engine (the same pattern
``tests/test_learning_api.py``'s helper uses) where the test's own focus
is GET/PATCH behavior, not creation itself.
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


def _requirements_path(fixtures: dict) -> str:
    return f"{_tracking_path(fixtures)}/requirements"


# --- route surface -------------------------------------------------------


def test_only_get_post_and_patch_routes_exist_put_and_delete_rejected(campaign_run_client: dict) -> None:
    """MVP-15B: POST now legitimately creates a Plan (201) instead of the
    old blanket 405 — PUT/DELETE remain unsupported on both the Plan
    route and the Requirement-creation sub-route."""
    fixtures = campaign_run_client
    path = _tracking_path(fixtures)
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].put(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405
    assert fixtures["client"].put(_requirements_path(fixtures), json={"name": "x"}).status_code == 405
    assert fixtures["client"].delete(_requirements_path(fixtures)).status_code == 405
    # Consume the POST route itself so this test does not leave a stray
    # Plan behind for tests that share the fixture's campaign — a
    # duplicate second creation is what actually proves 201 happened.
    created = fixtures["client"].post(path, headers=headers)
    assert created.status_code == 201, created.text
    assert fixtures["client"].post(path, headers=headers).status_code == 409


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


# --- CREATE Plan (MVP-15B) -------------------------------------------------


def test_create_plan_happy_path(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(_tracking_path(fixtures), headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert response.status_code == 201, response.text
    plan = response.json()["plan"]
    assert plan["status"] == "NOT_DEFINED"
    assert plan["requirements"] == []
    assert plan["id"].startswith("TRK-")


def test_create_plan_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.post("/api/v1/campaigns/CMP-FAKE00000000/tracking")
    assert response.status_code == 401


def test_create_plan_requires_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(_tracking_path(fixtures))
    assert response.status_code == 403


def test_create_plan_cross_tenant_rejected_non_leakily(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()

    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    response = client_a.post(
        f"/api/v1/campaigns/{body_b['campaign']['id']}/tracking", headers={"X-CSRF-Token": csrf_a}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_create_plan_second_sequential_creation_is_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    first = fixtures["client"].post(_tracking_path(fixtures), headers=headers)
    assert first.status_code == 201
    second = fixtures["client"].post(_tracking_path(fixtures), headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "TRACKING_PLAN_ALREADY_EXISTS"


# --- CREATE Requirement (MVP-15B) ------------------------------------------


def test_create_requirement_happy_path(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    fixtures["client"].post(_tracking_path(fixtures), headers=headers)
    response = fixtures["client"].post(_requirements_path(fixtures), json={"name": "Purchase event"}, headers=headers)
    assert response.status_code == 201, response.text
    requirements = response.json()["plan"]["requirements"]
    assert len(requirements) == 1
    assert requirements[0]["name"] == "Purchase event"
    assert requirements[0]["status"] is None
    assert requirements[0]["id"].startswith("TRQ-")


def test_create_requirement_duplicate_names_create_distinct_rows(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    fixtures["client"].post(_tracking_path(fixtures), headers=headers)
    first = fixtures["client"].post(_requirements_path(fixtures), json={"name": "Purchase event"}, headers=headers)
    second = fixtures["client"].post(_requirements_path(fixtures), json={"name": "Purchase event"}, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    requirements = second.json()["plan"]["requirements"]
    assert len(requirements) == 2
    assert requirements[0]["id"] != requirements[1]["id"]
    assert {r["name"] for r in requirements} == {"Purchase event"}


def test_create_requirement_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.post("/api/v1/campaigns/CMP-FAKE00000000/tracking/requirements", json={"name": "x"})
    assert response.status_code == 401


def test_create_requirement_requires_csrf(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    fixtures["client"].post(_tracking_path(fixtures), headers={"X-CSRF-Token": fixtures["csrf_token"]})
    response = fixtures["client"].post(_requirements_path(fixtures), json={"name": "x"})
    assert response.status_code == 403


def test_create_requirement_with_no_plan_is_forbidden(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    response = fixtures["client"].post(
        _requirements_path(fixtures), json={"name": "Purchase event"}, headers={"X-CSRF-Token": fixtures["csrf_token"]}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_create_requirement_in_forbidden_plan_state_is_conflict(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    from tests.contenttest import make_user
    from tests.trackingtest import advance_plan_to

    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        service = TrackingService(session)
        plan = service.record_tracking_plan(campaign=campaign)
        advance_plan_to(session, campaign, plan, TrackingReadinessStatus.CONFIGURED)

    response = fixtures["client"].post(
        _requirements_path(fixtures), json={"name": "Late requirement"}, headers={"X-CSRF-Token": fixtures["csrf_token"]}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TRACKING_REQUIREMENT_MUTATION_FORBIDDEN"


def test_create_requirement_empty_name_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    fixtures["client"].post(_tracking_path(fixtures), headers=headers)
    response = fixtures["client"].post(_requirements_path(fixtures), json={"name": ""}, headers=headers)
    assert response.status_code == 422


def test_create_requirement_name_too_long_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    fixtures["client"].post(_tracking_path(fixtures), headers=headers)
    response = fixtures["client"].post(_requirements_path(fixtures), json={"name": "x" * 256}, headers=headers)
    assert response.status_code == 422


def test_create_requirement_extra_field_rejected(campaign_run_client: dict) -> None:
    fixtures = campaign_run_client
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    fixtures["client"].post(_tracking_path(fixtures), headers=headers)
    response = fixtures["client"].post(
        _requirements_path(fixtures), json={"name": "Purchase event", "kind": "pixel"}, headers=headers
    )
    assert response.status_code == 422


# --- full manual flow: PATCH is now genuinely production-reachable ---------


def test_full_manual_flow_create_plan_requirement_transition_and_certify(campaign_run_client: dict) -> None:
    """Proves the whole MVP-15B contract end to end through public HTTP
    only: create Plan -> create Requirement -> legal transitions ->
    CERTIFIED, with CERTIFIED remaining a manual API-level transition and
    never implying any external verification."""
    fixtures = campaign_run_client
    headers = {"X-CSRF-Token": fixtures["csrf_token"]}
    client = fixtures["client"]

    created = client.post(_tracking_path(fixtures), headers=headers)
    assert created.status_code == 201

    with_requirement = client.post(_requirements_path(fixtures), json={"name": "Purchase event"}, headers=headers)
    assert with_requirement.status_code == 201
    requirement_id = with_requirement.json()["plan"]["requirements"][0]["id"]

    for target in (
        "REQUIREMENTS_DEFINED",
        "CONFIGURATION_PENDING",
        "CONFIGURED",
        "VERIFICATION_PENDING",
    ):
        response = client.patch(
            _tracking_path(fixtures), json={"operation": "TRANSITION_PLAN", "target_status": target}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["plan"]["status"] == target

    status_update = client.patch(
        _tracking_path(fixtures),
        json={"operation": "UPDATE_REQUIREMENT_STATUS", "requirement_id": requirement_id, "status": "Implementado"},
        headers=headers,
    )
    assert status_update.status_code == 200
    assert status_update.json()["plan"]["requirements"][0]["status"] == "Implementado"

    certified = client.patch(
        _tracking_path(fixtures), json={"operation": "TRANSITION_PLAN", "target_status": "CERTIFIED"}, headers=headers
    )
    assert certified.status_code == 200, certified.text
    assert certified.json()["plan"]["status"] == "CERTIFIED"
    # No response field of any kind implies external verification —
    # the frozen public DTO (TrackingPlanPublic/TrackingRequirementPublic)
    # never carried a provider/verification field to begin with.
    assert set(certified.json()["plan"].keys()) == {"id", "status", "requirements"}
