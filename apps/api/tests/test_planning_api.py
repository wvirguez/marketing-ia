"""API contract, tenancy, and security tests for the Planning read surface
(BACKEND-09 §18). All marked `postgres`.

No public write endpoint exists, so test data is recorded via
``PlanningService`` against the live app's own engine (the same pattern
``tests/test_strategy_api.py``'s helper uses), then read back through the
real, authenticated HTTP client.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as OrmSession

from app.campaigns.models import CampaignRunStatus
from app.campaigns.repository import CampaignRepository, CampaignRunRepository
from app.orchestration.models import BusinessStage, StageExecutionStatus
from app.orchestration.repository import RunStageExecutionRepository
from app.persistence.session import get_engine
from app.planning.service import PlanningService
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.orchestrationtest import initialize_run
from tests.planningtest import default_plan_item

pytestmark = pytest.mark.postgres

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _record_plan(fixtures: dict) -> None:
    """Records one Content Plan (+ two Plan Items) for `fixtures`'s
    campaign/run, via a genuinely separate, immediately-committed session
    bound to the app's own engine — so the write is visible to the next
    HTTP request the test client makes."""
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        run = CampaignRunRepository(session).get_by_public_id(fixtures["run_id"])
        stages = RunStageExecutionRepository(session).list_for_run(campaign_run_id=run.id)
        stages_by_name = {s.stage: s for s in stages}
        PlanningService(session).record_plan(
            campaign=campaign, campaign_run=run, stage_execution=stages_by_name[BusinessStage.PLAN],
            summary="A two-week content calendar introducing the training method.",
            items=[default_plan_item(), default_plan_item(format="Carrusel", sequence=2)],
        )


@pytest.fixture()
def campaign_client_with_stages(campaign_run_client: dict) -> dict:
    initialize_run(campaign_run_client)
    return campaign_run_client


# --- route surface -------------------------------------------------------


def test_only_get_routes_exist_for_plan(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    path = f"/api/v1/campaigns/{fixtures['campaign_id']}/plan"
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].post(path, json={}).status_code == 405
    assert fixtures["client"].put(path, json={}).status_code == 405
    assert fixtures["client"].patch(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405


# --- response shape --------------------------------------------------------


def test_empty_plan_output_for_a_campaign_with_no_plan(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/plan")
    assert response.status_code == 200
    assert response.json() == {"plan": None, "items": []}


def test_plan_output_returns_combined_aggregate(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_plan(fixtures)

    body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/plan").json()
    assert body["plan"]["id"].startswith("PLN-")
    assert body["plan"]["version"] == 1
    assert len(body["items"]) == 2
    assert all(item["id"].startswith("ITM-") for item in body["items"])
    assert {item["format"] for item in body["items"]} == {"Reel", "Carrusel"}


def test_plan_output_returns_the_current_highest_version(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_plan(fixtures)
    _record_plan(fixtures)  # second version

    body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/plan").json()
    assert body["plan"]["version"] == 2


# --- security --------------------------------------------------------------


def test_no_raw_uuid_in_plan_response(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_plan(fixtures)
    text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/plan").text
    assert not _UUID_RE.search(text), "plan response leaked a raw UUID"


def test_no_agent_identifiers_in_plan_response(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_plan(fixtures)
    text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/plan").text
    assert "AGENT-" not in text
    assert "agent_id" not in text


def test_no_secret_reasoning_or_governance_fields_in_response(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_plan(fixtures)
    text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/plan").text.lower()
    for forbidden in (
        "chain_of_thought", "reasoning", "api_key", "password", "provider",
        "approved", "approval", "ready_for_production", "gate_decision", "strategic_decision", "briefed",
    ):
        assert forbidden not in text


def test_plan_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/campaigns/CMP-FAKE00000000/plan")
    assert response.status_code == 401


# --- tenancy -----------------------------------------------------------


def test_tenant_a_cannot_read_tenant_bs_plan(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()

    response = client_a.get(f"/api/v1/campaigns/{body_b['campaign']['id']}/plan")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_unknown_campaign_id_is_indistinguishable_from_not_yours(campaign_client_with_stages: dict) -> None:
    response = campaign_client_with_stages["client"].get("/api/v1/campaigns/CMP-TOTALLYFAKE0/plan")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- stage-lifecycle non-mutation -----------------------------------------


def test_persisting_plan_does_not_change_run_or_stage_status(campaign_client_with_stages: dict, db_session) -> None:
    fixtures = campaign_client_with_stages
    _record_plan(fixtures)

    run = CampaignRunRepository(db_session).get_by_public_id(fixtures["run_id"])
    assert run.status is CampaignRunStatus.CREATED

    stages = RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)
    for stage in stages:
        assert stage.status is StageExecutionStatus.PENDING
