"""API contract, tenancy, and security tests for the Strategy read surface
(BACKEND-08 §19). All marked `postgres`.

No public write endpoint exists, so test data is recorded via
``StrategyService`` against the live app's own engine (the same pattern
``tests/test_research_api.py``'s helper uses), then read back through the
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
from app.strategy.service import StrategyService
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.orchestrationtest import initialize_run, run_path
from tests.strategytest import default_experiment, default_hypothesis

pytestmark = pytest.mark.postgres

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _record_strategy(fixtures: dict) -> None:
    """Records one Strategy (+positioning+hypothesis+experiment) for
    `fixtures`'s campaign/run, via a genuinely separate, immediately-
    committed session bound to the app's own engine — so the write is
    visible to the next HTTP request the test client makes."""
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        run = CampaignRunRepository(session).get_by_public_id(fixtures["run_id"])
        stages = RunStageExecutionRepository(session).list_for_run(campaign_run_id=run.id)
        stages_by_name = {s.stage: s for s in stages}
        StrategyService(session).record_strategy(
            campaign=campaign, campaign_run=run, stage_execution=stages_by_name[BusinessStage.STRATEGY],
            summary="Position as the structured, beginner-friendly training method.",
            positioning_statement="For first-time dog owners who feel overwhelmed by generic advice.",
            hypotheses=[default_hypothesis(experiments=[default_experiment()])],
        )


@pytest.fixture()
def campaign_client_with_stages(campaign_run_client: dict) -> dict:
    initialize_run(campaign_run_client)
    return campaign_run_client


# --- route surface -------------------------------------------------------


def test_only_get_routes_exist_for_strategy(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    path = f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy"
    assert fixtures["client"].get(path).status_code == 200
    assert fixtures["client"].post(path, json={}).status_code == 405
    assert fixtures["client"].patch(path, json={}).status_code == 405
    assert fixtures["client"].delete(path).status_code == 405


# --- response shape --------------------------------------------------------


def test_empty_strategy_output_for_a_campaign_with_no_strategy(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    response = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy")
    assert response.status_code == 200
    assert response.json() == {"strategy": None, "positioning": None, "hypotheses": [], "experiments": []}


def test_strategy_output_returns_combined_aggregate(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_strategy(fixtures)

    body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()
    assert body["strategy"]["id"].startswith("STR-")
    assert body["strategy"]["version"] == 1
    assert body["positioning"]["id"].startswith("POS-")
    assert len(body["hypotheses"]) == 1
    assert body["hypotheses"][0]["id"].startswith("HYP-")
    assert body["hypotheses"][0]["status"] == "OPEN"
    assert len(body["experiments"]) == 1
    assert body["experiments"][0]["id"].startswith("EXP-")
    assert body["experiments"][0]["hypothesis_id"] == body["hypotheses"][0]["id"]
    assert body["experiments"][0]["status"] is None


def test_strategy_output_returns_the_current_highest_version(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_strategy(fixtures)
    _record_strategy(fixtures)  # second version

    body = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").json()
    assert body["strategy"]["version"] == 2


# --- security --------------------------------------------------------------


def test_no_raw_uuid_in_strategy_response(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_strategy(fixtures)
    text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").text
    assert not _UUID_RE.search(text), "strategy response leaked a raw UUID"


def test_no_agent_identifiers_in_strategy_response(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_strategy(fixtures)
    text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").text
    assert "AGENT-" not in text
    assert "agent_id" not in text


def test_no_secret_reasoning_or_governance_fields_in_response(campaign_client_with_stages: dict) -> None:
    fixtures = campaign_client_with_stages
    _record_strategy(fixtures)
    text = fixtures["client"].get(f"/api/v1/campaigns/{fixtures['campaign_id']}/strategy").text.lower()
    for forbidden in (
        "chain_of_thought", "reasoning", "api_key", "password", "provider",
        "approved", "approval", "maturity", "gate_decision", "strategic_decision",
    ):
        assert forbidden not in text


def test_strategy_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/campaigns/CMP-FAKE00000000/strategy")
    assert response.status_code == 401


# --- tenancy -----------------------------------------------------------


def test_tenant_a_cannot_read_tenant_bs_strategy(auth_client: TestClient) -> None:
    client_a = auth_client
    register_and_get_csrf(client_a, display_name="User A")

    client_b = TestClient(auth_client.app, raise_server_exceptions=False)
    register_and_get_csrf(client_b, display_name="User B")
    csrf_b = client_b.get("/api/v1/auth/csrf").json()["csrf_token"]
    body_b = client_b.post("/api/v1/campaigns", json=campaign_payload(name="B"), headers={"X-CSRF-Token": csrf_b}).json()

    response = client_a.get(f"/api/v1/campaigns/{body_b['campaign']['id']}/strategy")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_unknown_campaign_id_is_indistinguishable_from_not_yours(campaign_client_with_stages: dict) -> None:
    response = campaign_client_with_stages["client"].get("/api/v1/campaigns/CMP-TOTALLYFAKE0/strategy")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- stage-lifecycle non-mutation -----------------------------------------


def test_persisting_strategy_does_not_change_run_or_stage_status(campaign_client_with_stages: dict, db_session) -> None:
    fixtures = campaign_client_with_stages
    _record_strategy(fixtures)

    run = CampaignRunRepository(db_session).get_by_public_id(fixtures["run_id"])
    assert run.status is CampaignRunStatus.CREATED

    stages = RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)
    for stage in stages:
        assert stage.status is StageExecutionStatus.PENDING
