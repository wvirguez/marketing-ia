"""Run lifecycle, stage lifecycle, and orchestration initialization/start
(BACKEND-06 §8/§9/§11/§12/§14/§15). All marked `postgres`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.campaigns.models import CampaignRunStatus
from app.core.api_errors import InvalidLifecycleTransitionError, OrchestrationNotInitializedError
from app.orchestration.models import BUSINESS_STAGE_ORDER, StageExecutionStatus
from app.orchestration.service import OrchestrationService
from tests.orchestrationtest import initialize_run, run_path, start_run

pytestmark = pytest.mark.postgres


# --- initialize ------------------------------------------------------------


def test_initialize_materializes_all_business_stages_in_order(campaign_run_client: dict) -> None:
    body = initialize_run(campaign_run_client)

    stages = body["items"]
    assert [s["stage"] for s in stages] == [s.value for s in BUSINESS_STAGE_ORDER]
    assert [s["ordinal"] for s in stages] == list(range(1, len(BUSINESS_STAGE_ORDER) + 1))
    assert all(s["status"] == "PENDING" for s in stages)
    assert all(s["id"].startswith("STG-") for s in stages)
    # No fake output: nothing beyond identity/ordering/status is populated.
    assert all(s["started_at"] is None and s["completed_at"] is None for s in stages)


def test_initialize_is_idempotent(campaign_run_client: dict) -> None:
    first = initialize_run(campaign_run_client)
    second = initialize_run(campaign_run_client)

    assert [s["id"] for s in first["items"]] == [s["id"] for s in second["items"]]

    stages_response = campaign_run_client["client"].get(run_path(campaign_run_client, "/stages"))
    assert len(stages_response.json()["items"]) == len(BUSINESS_STAGE_ORDER)


def test_initialize_requires_csrf(campaign_run_client: dict) -> None:
    response = campaign_run_client["client"].post(run_path(campaign_run_client, "/initialize"))
    assert response.status_code == 403


def test_initialize_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.post("/api/v1/campaigns/CMP-FAKE00000000/runs/RUN-FAKE00000000/initialize")
    assert response.status_code == 401


# --- start -------------------------------------------------------------------


def test_start_requires_prior_initialization(campaign_run_client: dict) -> None:
    response = campaign_run_client["client"].post(
        run_path(campaign_run_client, "/start"), headers={"X-CSRF-Token": campaign_run_client["csrf_token"]}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ORCHESTRATION_NOT_INITIALIZED"


def test_start_transitions_run_to_running(campaign_run_client: dict) -> None:
    initialize_run(campaign_run_client)
    body = start_run(campaign_run_client)
    assert body["status"] == "RUNNING"


def test_start_runs_the_mvp04_deterministic_bootstrap_through_plan(campaign_run_client: dict) -> None:
    """MVP-04: starting a run now also synchronously executes the
    deterministic Research/Audience/Strategy/Plan bootstrap (an
    authorized, deliberate extension of BACKEND-06's own `start_run` —
    see the MVP-04 Phase 1 architecture gate). CONTENT and every later
    business stage remain untouched at PENDING; nothing beyond PLAN is
    ever promoted."""
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    stages = campaign_run_client["client"].get(run_path(campaign_run_client, "/stages")).json()["items"]
    by_stage = {s["stage"]: s["status"] for s in stages}
    assert by_stage["RESEARCH"] == "COMPLETED"
    assert by_stage["AUDIENCE"] == "COMPLETED"
    assert by_stage["STRATEGY"] == "COMPLETED"
    assert by_stage["PLAN"] == "COMPLETED"
    for pending_stage in ("CONTENT", "CREATIVE", "DISTRIBUTION", "PAID_MEDIA", "TRACKING", "MEASUREMENT", "LEARNING"):
        assert by_stage[pending_stage] == "PENDING", pending_stage


def test_start_is_not_idempotent(campaign_run_client: dict) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    second = campaign_run_client["client"].post(
        run_path(campaign_run_client, "/start"), headers={"X-CSRF-Token": campaign_run_client["csrf_token"]}
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"


def test_start_requires_csrf(campaign_run_client: dict) -> None:
    initialize_run(campaign_run_client)
    response = campaign_run_client["client"].post(run_path(campaign_run_client, "/start"))
    assert response.status_code == 403


# --- no client-controlled completion (BACKEND-06 §9) ------------------------


def test_no_generic_status_mutation_endpoint_exists(campaign_run_client: dict) -> None:
    """There is no PATCH for a run at all — status only ever changes
    through the dedicated initialize/start/respond operations."""
    response = campaign_run_client["client"].patch(
        run_path(campaign_run_client), json={"status": "COMPLETED"}, headers={"X-CSRF-Token": campaign_run_client["csrf_token"]}
    )
    assert response.status_code == 405


# --- reads -------------------------------------------------------------------


def test_get_run_detail(campaign_run_client: dict) -> None:
    response = campaign_run_client["client"].get(run_path(campaign_run_client))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == campaign_run_client["run_id"]
    assert body["status"] == "CREATED"


def test_progress_reflects_run_and_stage_state(campaign_run_client: dict) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    body = campaign_run_client["client"].get(run_path(campaign_run_client, "/progress")).json()
    assert body["campaign_id"] == campaign_run_client["campaign_id"]
    assert body["run_id"] == campaign_run_client["run_id"]
    assert body["run_status"] == "RUNNING"
    # MVP-04: start now also runs the deterministic bootstrap through
    # PLAN, so the first non-terminal stage is CONTENT, not RESEARCH.
    assert body["current_stage"] == "CONTENT"
    assert body["waiting_for_input"] is False
    assert body["open_decision_count"] == 0
    assert len(body["stages"]) == len(BUSINESS_STAGE_ORDER)


def test_progress_never_exposes_a_percentage_or_agent_ids(campaign_run_client: dict) -> None:
    initialize_run(campaign_run_client)
    response = campaign_run_client["client"].get(run_path(campaign_run_client, "/progress"))
    body_text = response.text
    assert "percentage" not in body_text and "percent" not in body_text
    for n in range(0, 11):
        assert f"AGENT-{n:02d}" not in body_text


def test_stages_returned_in_deterministic_order(campaign_run_client: dict) -> None:
    initialize_run(campaign_run_client)
    response = campaign_run_client["client"].get(run_path(campaign_run_client, "/stages"))
    stages = response.json()["items"]
    assert [s["ordinal"] for s in stages] == sorted(s["ordinal"] for s in stages)


# --- centralized transition matrix (service-level; BACKEND-06 §19 permits
# constructing controlled domain fixtures directly) --------------------------


def test_invalid_run_transition_is_rejected(db_session) -> None:
    from app.campaigns.repository import CampaignRepository, CampaignRunRepository
    from app.workspaces.repository import OrganizationRepository, WorkspaceRepository

    organization = OrganizationRepository(db_session).create(name="Transition Test Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="Transition Test WS")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="Transition Test Campaign")
    run = CampaignRunRepository(db_session).create(campaign=campaign, run_number=1)
    db_session.flush()

    assert run.status is CampaignRunStatus.CREATED
    service = OrchestrationService(db_session)
    with pytest.raises(InvalidLifecycleTransitionError):
        service._transition_run(
            run=run, target=CampaignRunStatus.COMPLETED, campaign_id=campaign.id, actor_user_id=None, request_id=None
        )


def test_terminal_run_state_cannot_transition_again(db_session) -> None:
    from app.campaigns.repository import CampaignRepository, CampaignRunRepository
    from app.workspaces.repository import OrganizationRepository, WorkspaceRepository

    organization = OrganizationRepository(db_session).create(name="Terminal Test Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="Terminal Test WS")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="Terminal Test Campaign")
    run = CampaignRunRepository(db_session).create(campaign=campaign, run_number=1)
    db_session.flush()

    service = OrchestrationService(db_session)
    service._transition_run(
        run=run, target=CampaignRunStatus.CANCELLED, campaign_id=campaign.id, actor_user_id=None, request_id=None
    )
    assert run.status is CampaignRunStatus.CANCELLED

    with pytest.raises(InvalidLifecycleTransitionError):
        service._transition_run(
            run=run, target=CampaignRunStatus.RUNNING, campaign_id=campaign.id, actor_user_id=None, request_id=None
        )


def test_terminal_stage_state_cannot_transition_again(db_session) -> None:
    from app.campaigns.repository import CampaignRepository, CampaignRunRepository
    from app.orchestration.repository import RunStageExecutionRepository
    from app.workspaces.repository import OrganizationRepository, WorkspaceRepository

    organization = OrganizationRepository(db_session).create(name="Stage Terminal Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="Stage Terminal WS")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="Stage Terminal Campaign")
    run = CampaignRunRepository(db_session).create(campaign=campaign, run_number=1)
    db_session.flush()
    stages = RunStageExecutionRepository(db_session).materialize_for_run(campaign_run=run)
    first_stage = stages[0]

    service = OrchestrationService(db_session)
    service._transition_stage(
        stage_execution=first_stage, target=StageExecutionStatus.SKIPPED, campaign_id=campaign.id, actor_user_id=None, request_id=None
    )
    assert first_stage.status is StageExecutionStatus.SKIPPED

    with pytest.raises(InvalidLifecycleTransitionError):
        service._transition_stage(
            stage_execution=first_stage, target=StageExecutionStatus.READY, campaign_id=campaign.id, actor_user_id=None, request_id=None
        )
