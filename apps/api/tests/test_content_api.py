"""API contract, tenancy, and security tests for the Content read surface
(BACKEND-10 §28/§29/§30). All marked `postgres`.

No public write endpoint exists, so test data is recorded via
``ContentService`` against the live app's own engine (the same pattern
``tests/test_planning_api.py``'s helper uses), then read back through the
real, authenticated HTTP client.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as OrmSession

from app.campaigns.repository import CampaignRepository, CampaignRunRepository
from app.content.service import ContentService
from app.orchestration.models import BusinessStage
from app.orchestration.repository import RunStageExecutionRepository
from app.persistence.session import get_engine
from app.planning.service import PlanningService
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.contenttest import default_piece_fields, default_version_payload
from tests.orchestrationtest import initialize_run
from tests.planningtest import default_plan_item

pytestmark = pytest.mark.postgres

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _record_content_piece(fixtures: dict) -> None:
    """Records one Content Plan + Plan Item + Content Brief + Content
    Piece + initial Content Version for `fixtures`'s campaign/run, via a
    genuinely separate, immediately-committed session bound to the app's
    own engine — so the write is visible to the next HTTP request the
    test client makes."""
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        run = CampaignRunRepository(session).get_by_public_id(fixtures["run_id"])
        stages = RunStageExecutionRepository(session).list_for_run(campaign_run_id=run.id)
        stages_by_name = {s.stage: s for s in stages}
        plan, items = PlanningService(session).record_plan(
            campaign=campaign, campaign_run=run, stage_execution=stages_by_name[BusinessStage.PLAN],
            summary="Plan.", items=[default_plan_item()],
        )
        content_service = ContentService(session)
        brief = content_service.record_brief(plan_item=items[0], content_plan=plan, brief="Produce a reel.")
        content_service.record_piece(
            content_brief=brief, initial_payload=default_version_payload(), **default_piece_fields()
        )


@pytest.fixture()
def campaign_client_with_stages(campaign_run_client: dict) -> dict:
    initialize_run(campaign_run_client)
    return campaign_run_client


# --- route surface -------------------------------------------------------


def test_only_get_routes_exist_for_content_list(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    path = f"/api/v1/campaigns/{fixtures['campaign_id']}/content"
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].post(path, json={}).status_code == 405
    assert fixtures["client"].put(path, json={}).status_code == 405
    assert fixtures["client"].patch(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405


def test_only_get_routes_exist_for_content_detail(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_content_piece(fixtures)
    body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").json()
    content_id = body["items"][0]["id"]
    path = f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}"
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].post(path, json={}).status_code == 405
    assert fixtures["client"].put(path, json={}).status_code == 405
    assert fixtures["client"].patch(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405


def test_no_content_brief_route_exists(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content-briefs")
    assert response.status_code == 404


def test_no_approval_route_exists(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_content_piece(fixtures)
    body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").json()
    content_id = body["items"][0]["id"]
    path = f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}/approvals"
    assert fixtures["client"].get(path).status_code == 404
    assert fixtures["client"].post(path, json={}).status_code == 404


# --- response shape --------------------------------------------------------


def test_empty_content_list_for_a_campaign_with_no_content(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content")
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_content_list_and_detail_return_expected_shape(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_content_piece(fixtures)

    list_body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").json()
    assert len(list_body["items"]) == 1
    item = list_body["items"][0]
    assert item["id"].startswith("CNT-")
    assert item["status"] == "DRAFT"
    assert item["format"] == "Reel"

    detail = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{item['id']}").json()
    assert detail["piece"]["id"] == item["id"]
    assert detail["latest_version"]["id"].startswith("CNV-")
    assert detail["latest_version"]["payload"]["kind"] == "reel"


def test_unknown_content_piece_id_is_indistinguishable_from_not_yours(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content/CNT-TOTALLYFAKE0")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- security --------------------------------------------------------------


def test_no_raw_uuid_in_content_responses(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_content_piece(fixtures)
    list_text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").text
    assert not _UUID_RE.search(list_text), "content list response leaked a raw UUID"

    body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").json()
    content_id = body["items"][0]["id"]
    detail_text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content/{content_id}").text
    assert not _UUID_RE.search(detail_text), "content detail response leaked a raw UUID"


def test_no_agent_identifiers_in_content_responses(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_content_piece(fixtures)
    text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").text
    assert "AGENT-" not in text
    assert "agent_id" not in text


def test_no_secret_reasoning_or_governance_fields_in_response(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_content_piece(fixtures)
    text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/content").text.lower()
    for forbidden in (
        "chain_of_thought", "reasoning", "api_key", "password", "provider",
        "reviewer", "approved_by", "gate_decision", "strategic_decision",
    ):
        assert forbidden not in text


def test_content_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/campaigns/CMP-FAKE00000000/content")
    assert response.status_code == 401


# --- tenancy -----------------------------------------------------------


def test_tenant_a_cannot_read_tenant_bs_content(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()

    response = client_a.get(f"/api/v1/campaigns/{body_b['campaign']['id']}/content")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_unknown_campaign_id_is_indistinguishable_from_not_yours(campaign_client_with_stages: dict) -> None:
    response = campaign_client_with_stages["client"].get("/api/v1/campaigns/CMP-TOTALLYFAKE0/content")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_content_piece_belonging_to_another_campaign_is_not_visible(campaign_client_with_stages: dict) -> None:
    fixtures_a = campaign_client_with_stages
    _record_content_piece(fixtures_a)
    body_a = fixtures_a["client"].get(f"/api/v1/campaigns/{fixtures_a['campaign_id']}/content").json()
    content_id = body_a["items"][0]["id"]

    csrf_b = fixtures_a["client"].get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = fixtures_a["client"].post(
        "/api/v1/campaigns", json=campaign_payload(name="Campaign B"), headers={"X-CSRF-Token": csrf_b}
    ).json()
    response = fixtures_a["client"].get(f"/api/v1/campaigns/{body_b['campaign']['id']}/content/{content_id}")
    assert response.status_code == 403
