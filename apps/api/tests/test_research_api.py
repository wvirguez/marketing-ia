"""API contract, tenancy, and security tests for the Research/Audience
read surface (BACKEND-07 §15/§16/§17). All marked `postgres`.

No public write endpoint exists, so test data is recorded via
``ResearchService`` against the live app's own engine (the same pattern
``tests/test_orchestration_hitl.py``'s ``_open_decision`` helper uses),
then read back through the real, authenticated HTTP client.
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
from app.research.service import ResearchService
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.orchestrationtest import initialize_run, run_path
from tests.researchtest import default_source, default_voc

pytestmark = pytest.mark.postgres

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _record_report_and_profile(fixtures: dict) -> None:
    """Records one ResearchReport (+source) and one AudienceProfile (+VOC
    item) for `fixtures`'s campaign/run, via a genuinely separate,
    immediately-committed session bound to the app's own engine — so the
    write is visible to the next HTTP request the test client makes."""
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        run = CampaignRunRepository(session).get_by_public_id(fixtures["run_id"])
        stages = RunStageExecutionRepository(session).list_for_run(campaign_run_id=run.id)
        stages_by_name = {s.stage: s for s in stages}
        service = ResearchService(session)
        service.record_report(
            campaign=campaign, campaign_run=run, stage_execution=stages_by_name[BusinessStage.RESEARCH],
            summary="Owners want a clear, progressive method.", sources=[default_source()],
        )
        service.record_audience_profile(
            campaign=campaign, campaign_run=run, stage_execution=stages_by_name[BusinessStage.AUDIENCE],
            summary="First-time dog owners.", voc_items=[default_voc()],
        )


@pytest.fixture()
def campaign_client_with_stages(campaign_run_client: dict) -> dict:
    initialize_run(campaign_run_client)
    return campaign_run_client


# --- route surface -------------------------------------------------------


def test_only_get_routes_exist_for_research(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    path = f"/api/v1/campaigns/{fixtures['campaign_id']}/research"
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].post(path, json={}).status_code == 405
    assert fixtures["client"].patch(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405


def test_only_get_routes_exist_for_audience(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    path = f"/api/v1/campaigns/{fixtures['campaign_id']}/audience"
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].post(path, json={}).status_code == 405
    assert fixtures["client"].patch(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405


# --- response shape --------------------------------------------------------


def test_empty_research_output_for_a_campaign_with_no_report(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/research")
    assert response.status_code == 200
    body = response.json()
    assert body == {"report": None, "sources": []}


def test_empty_audience_output_for_a_campaign_with_no_profile(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/audience")
    assert response.status_code == 200
    body = response.json()
    assert body == {"profile": None, "voc_evidence": []}


def test_research_output_returns_combined_report_and_sources(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_report_and_profile(fixtures)

    body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/research").json()
    assert body["report"]["id"].startswith("RPT-")
    assert body["report"]["version"] == 1
    assert len(body["sources"]) == 1
    assert body["sources"][0]["id"].startswith("SRCE-")
    assert body["sources"][0]["source_type"] == "ARTICLE"


def test_audience_output_returns_combined_profile_and_voc(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_report_and_profile(fixtures)

    body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/audience").json()
    assert body["profile"]["id"].startswith("AUD-")
    assert body["profile"]["version"] == 1
    assert len(body["voc_evidence"]) == 1
    assert body["voc_evidence"][0]["id"].startswith("VOC-")


def test_research_output_returns_the_current_highest_version(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_report_and_profile(fixtures)
    _record_report_and_profile(fixtures)  # second version

    body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/research").json()
    assert body["report"]["version"] == 2


# --- security --------------------------------------------------------------


def test_no_raw_uuid_in_research_or_audience_response(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_report_and_profile(fixtures)
    for path in ("research", "audience"):
        text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/{path}").text
        assert not _UUID_RE.search(text), f"{path} response leaked a raw UUID"


def test_no_agent_identifiers_in_research_or_audience_response(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_report_and_profile(fixtures)
    for path in ("research", "audience"):
        text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/{path}").text
        assert "AGENT-" not in text
        assert "agent_id" not in text


def test_no_secret_or_reasoning_fields_in_response(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_report_and_profile(fixtures)
    for path in ("research", "audience"):
        text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/{path}").text
        for forbidden in ("chain_of_thought", "reasoning", "api_key", "password", "provider"):
            assert forbidden not in text.lower()


def test_research_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/campaigns/CMP-FAKE00000000/research")
    assert response.status_code == 401


def test_audience_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/campaigns/CMP-FAKE00000000/audience")
    assert response.status_code == 401


# --- tenancy -----------------------------------------------------------


def test_tenant_a_cannot_read_tenant_bs_research(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")
    csrf_a = client_a.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_a = client_a.post("/api/v1/campaigns", json=campaign_payload(name="A"), headers={"X-CSRF-Token": csrf_a}).json()

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()

    response = client_a.get(f"/api/v1/campaigns/{body_b['campaign']['id']}/research")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_tenant_a_cannot_read_tenant_bs_audience(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()

    response = client_a.get(f"/api/v1/campaigns/{body_b['campaign']['id']}/audience")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_unknown_campaign_id_is_indistinguishable_from_not_yours(campaign_client_with_stages: dict) -> None:
    response = campaign_client_with_stages["client"].get("/api/v1/campaigns/CMP-TOTALLYFAKE0/research")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- stage-lifecycle non-mutation -----------------------------------------


def test_persisting_research_does_not_change_run_or_stage_status(campaign_client_with_stages: dict, db_session) -> None:
    fixtures = campaign_client_with_stages
    _record_report_and_profile(fixtures)

    run = CampaignRunRepository(db_session).get_by_public_id(fixtures["run_id"])
    assert run.status is CampaignRunStatus.CREATED  # unchanged — initialize_run never starts the run

    stages = RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)
    for stage in stages:
        assert stage.status is StageExecutionStatus.PENDING  # unchanged by research/audience persistence
