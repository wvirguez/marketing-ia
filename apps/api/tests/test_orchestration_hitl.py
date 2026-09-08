"""Human-in-the-loop foundation: HumanDecisionRequest / HumanDecisionResponse
(BACKEND-06 §16-19). No public HTTP endpoint creates a decision request —
BACKEND-06 exposes no legitimate production trigger for one yet, so every
test here constructs the request through the same controlled
service-level mechanism a future runtime phase will call
(``OrchestrationService.create_decision_request``), exactly as permitted
by BACKEND-06 §19.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from app.campaigns.models import CampaignRunStatus
from app.campaigns.repository import CampaignRepository, CampaignRunRepository
from app.core.api_errors import InvalidLifecycleTransitionError
from app.orchestration.models import HumanDecisionResponse
from app.orchestration.service import OrchestrationService
from app.persistence.session import get_engine
from app.users.repository import UserRepository
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.orchestrationtest import initialize_run, run_path, start_run

pytestmark = pytest.mark.postgres


def _open_decision(fixtures: dict, db_session) -> dict:
    """Raises a real, persisted, committed OPEN decision against
    `fixtures`'s already-RUNNING run, using the same DB connection the
    HTTP client's own session is bound to isn't required here — the
    campaign fixture and this helper both go through the same
    `TEST_DATABASE_URL` the running app is configured against, so a
    directly-committed write here is visible to the next HTTP request.
    """
    engine = get_engine()
    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        run = CampaignRunRepository(session).get_by_public_id(fixtures["run_id"])
        request = OrchestrationService(session).create_decision_request(
            campaign=campaign,
            run=run,
            question="Which positioning should this campaign lead with?",
            stage_execution_id=None,
            actor_user_id=None,
            request_id=None,
        )
        return {"public_id": request.public_id, "question": request.question}


def test_create_decision_request_requires_a_running_run(db_session) -> None:
    organization = OrganizationRepository(db_session).create(name="HITL Guard Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="HITL Guard WS")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="HITL Guard Campaign")
    run = CampaignRunRepository(db_session).create(campaign=campaign, run_number=1)
    db_session.flush()
    assert run.status is CampaignRunStatus.CREATED

    with pytest.raises(InvalidLifecycleTransitionError):
        OrchestrationService(db_session).create_decision_request(
            campaign=campaign, run=run, question="?", stage_execution_id=None, actor_user_id=None, request_id=None
        )


def test_opening_a_decision_forces_run_awaiting_human_decision(db_session) -> None:
    organization = OrganizationRepository(db_session).create(name="HITL Force Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="HITL Force WS")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="HITL Force Campaign")
    run = CampaignRunRepository(db_session).create(campaign=campaign, run_number=1)
    db_session.flush()
    service = OrchestrationService(db_session)
    service._transition_run(
        run=run, target=CampaignRunStatus.RUNNING, campaign_id=campaign.id, actor_user_id=None, request_id=None
    )

    service.create_decision_request(
        campaign=campaign, run=run, question="Budget tier?", stage_execution_id=None, actor_user_id=None, request_id=None
    )

    assert run.status is CampaignRunStatus.AWAITING_HUMAN_DECISION


def test_open_decision_appears_in_listing_and_progress(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)
    _open_decision(campaign_run_client, db_session)

    listing = campaign_run_client["client"].get(run_path(campaign_run_client, "/decisions")).json()
    assert listing["total"] == 1
    assert listing["items"][0]["status"] == "OPEN"
    assert listing["items"][0]["id"].startswith("HDR-")
    assert listing["items"][0]["response"] is None

    progress = campaign_run_client["client"].get(run_path(campaign_run_client, "/progress")).json()
    assert progress["run_status"] == "AWAITING_HUMAN_DECISION"
    assert progress["waiting_for_input"] is True
    assert progress["open_decision_count"] == 1


def test_respond_to_decision_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/campaigns/CMP-FAKE00000000/runs/RUN-FAKE00000000/decisions/HDR-FAKE0000000/respond",
        json={"response_text": "Option A"},
    )
    assert response.status_code == 401


def test_respond_to_decision_requires_csrf(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)
    decision = _open_decision(campaign_run_client, db_session)

    response = campaign_run_client["client"].post(
        run_path(campaign_run_client, f"/decisions/{decision['public_id']}/respond"),
        json={"response_text": "Option A"},
    )
    assert response.status_code == 403


def test_respond_to_decision_resolves_it_and_resumes_the_run(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)
    decision = _open_decision(campaign_run_client, db_session)

    response = campaign_run_client["client"].post(
        run_path(campaign_run_client, f"/decisions/{decision['public_id']}/respond"),
        json={"response_text": "Go with positioning B."},
        headers={"X-CSRF-Token": campaign_run_client["csrf_token"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["response_text"] == "Go with positioning B."
    assert body["id"].startswith("HDS-")

    run_detail = campaign_run_client["client"].get(run_path(campaign_run_client)).json()
    assert run_detail["status"] == "RUNNING"

    listing = campaign_run_client["client"].get(run_path(campaign_run_client, "/decisions")).json()
    assert listing["items"][0]["status"] == "RESOLVED"
    assert listing["items"][0]["response"]["response_text"] == "Go with positioning B."


def test_cannot_respond_to_the_same_decision_twice(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)
    decision = _open_decision(campaign_run_client, db_session)

    first = campaign_run_client["client"].post(
        run_path(campaign_run_client, f"/decisions/{decision['public_id']}/respond"),
        json={"response_text": "First answer."},
        headers={"X-CSRF-Token": campaign_run_client["csrf_token"]},
    )
    assert first.status_code == 200

    second = campaign_run_client["client"].post(
        run_path(campaign_run_client, f"/decisions/{decision['public_id']}/respond"),
        json={"response_text": "Second answer — should be rejected."},
        headers={"X-CSRF-Token": campaign_run_client["csrf_token"]},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DECISION_ALREADY_RESOLVED"


def test_cross_tenant_response_is_denied(campaign_run_client: dict, db_session, auth_client: TestClient) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)
    decision = _open_decision(campaign_run_client, db_session)

    other_client = TestClient(auth_client.app, raise_server_exceptions=False)
    other_csrf = register_and_get_csrf(other_client, display_name="Outsider")

    response = other_client.post(
        run_path(campaign_run_client, f"/decisions/{decision['public_id']}/respond"),
        json={"response_text": "I should not be able to do this."},
        headers={"X-CSRF-Token": other_csrf},
    )
    assert response.status_code == 403


def test_response_history_is_preserved_not_overwritten(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)
    decision = _open_decision(campaign_run_client, db_session)

    campaign_run_client["client"].post(
        run_path(campaign_run_client, f"/decisions/{decision['public_id']}/respond"),
        json={"response_text": "Kept forever."},
        headers={"X-CSRF-Token": campaign_run_client["csrf_token"]},
    )

    stored = db_session.execute(select(HumanDecisionResponse)).scalars().all()
    matching = [r for r in stored if r.response_text == "Kept forever."]
    assert len(matching) == 1


def test_decision_response_uniqueness_is_enforced_at_the_database_level(db_session) -> None:
    organization = OrganizationRepository(db_session).create(name="HDS Unique Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="HDS Unique WS")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="HDS Unique Campaign")
    run = CampaignRunRepository(db_session).create(campaign=campaign, run_number=1)
    db_session.flush()
    service = OrchestrationService(db_session)
    service._transition_run(
        run=run, target=CampaignRunStatus.RUNNING, campaign_id=campaign.id, actor_user_id=None, request_id=None
    )
    request = service.create_decision_request(
        campaign=campaign, run=run, question="?", stage_execution_id=None, actor_user_id=None, request_id=None
    )
    user = UserRepository(db_session).create(
        email="hds-unique@example.com",
        normalized_email="hds-unique@example.com",
        password_hash="not-a-real-hash",
        display_name="HDS Test",
    )
    db_session.flush()

    db_session.add(
        HumanDecisionResponse(
            public_id="HDS-UNIQUETEST1",
            workspace_id=workspace.id,
            decision_request_id=request.id,
            responded_by_user_id=user.id,
            response_text="first",
        )
    )
    db_session.flush()

    db_session.add(
        HumanDecisionResponse(
            public_id="HDS-UNIQUETEST2",
            workspace_id=workspace.id,
            decision_request_id=request.id,
            responded_by_user_id=user.id,
            response_text="second, should collide",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
