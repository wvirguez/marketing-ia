"""Append-only Audit Event traceability (BACKEND-06 §20/§21/§28).
All marked `postgres`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.campaigns.models import CampaignRunStatus
from app.campaigns.repository import CampaignRepository, CampaignRunRepository
from app.orchestration.models import HumanDecisionRequest, StageExecutionStatus
from app.orchestration.repository import RunStageExecutionRepository
from app.orchestration.service import OrchestrationService
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.orchestrationtest import initialize_run, run_path, start_run

pytestmark = pytest.mark.postgres


def test_initialize_emits_an_event(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)

    events = campaign_run_client["client"].get(run_path(campaign_run_client, "/events")).json()
    assert events["total"] >= 1
    assert any(e["event_type"] == "orchestration.initialized" for e in events["items"])


def test_start_emits_run_and_stage_transition_events_with_previous_and_new_state(campaign_run_client: dict) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    events = campaign_run_client["client"].get(run_path(campaign_run_client, "/events")).json()["items"]

    run_event = next(e for e in events if e["event_type"] == "orchestration.run.transitioned")
    assert run_event["previous_state"] == "CREATED"
    assert run_event["new_state"] == "RUNNING"

    stage_event = next(e for e in events if e["event_type"] == "orchestration.stage.transitioned")
    assert stage_event["previous_state"] == "PENDING"
    assert stage_event["new_state"] == "READY"


def test_events_record_the_authenticated_actor(campaign_run_client: dict) -> None:
    initialize_run(campaign_run_client)

    events = campaign_run_client["client"].get(run_path(campaign_run_client, "/events")).json()["items"]
    event = events[0]
    assert event["actor_type"] == "USER"
    assert event["actor_user_id"] is not None
    assert event["actor_user_id"].startswith("USR-")


def test_events_are_ordered_chronologically(campaign_run_client: dict) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    events = campaign_run_client["client"].get(run_path(campaign_run_client, "/events")).json()["items"]
    timestamps = [e["created_at"] for e in events]
    assert timestamps == sorted(timestamps)


def test_events_are_tenant_scoped_to_this_run_only(campaign_run_client: dict, db_session) -> None:
    """A second, unrelated run's events in the same database must never
    leak into this run's event listing."""
    organization = OrganizationRepository(db_session).create(name="Isolation Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="Isolation WS")
    other_campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="Other Campaign")
    other_run = CampaignRunRepository(db_session).create(campaign=other_campaign, run_number=1)
    other_event = AuditEventRepository(db_session).record(
        workspace_id=workspace.id,
        event_type="orchestration.initialized",
        actor_type=ActorType.SYSTEM,
        campaign_id=other_campaign.id,
        campaign_run_id=other_run.id,
    )
    db_session.commit()

    initialize_run(campaign_run_client)
    events = campaign_run_client["client"].get(run_path(campaign_run_client, "/events")).json()["items"]

    event_ids = {e["id"] for e in events}
    assert other_event.public_id not in event_ids


def test_no_mutation_or_deletion_endpoint_exists_for_events(campaign_run_client: dict) -> None:
    initialize_run(campaign_run_client)
    events = campaign_run_client["client"].get(run_path(campaign_run_client, "/events")).json()["items"]
    event_id = events[0]["id"]

    patch_response = campaign_run_client["client"].patch(
        run_path(campaign_run_client, "/events"), json={"id": event_id, "event_type": "tampered"}
    )
    assert patch_response.status_code == 405

    delete_response = campaign_run_client["client"].delete(run_path(campaign_run_client, "/events"))
    assert delete_response.status_code == 405


def test_events_list_requires_authentication(campaign_run_client: dict) -> None:
    campaign_run_client["client"].cookies.clear()
    response = campaign_run_client["client"].get(run_path(campaign_run_client, "/events"))
    assert response.status_code == 401


def test_failed_event_persistence_rolls_back_the_state_transition(db_session) -> None:
    """Directly exercises OrchestrationService.start_run() so a forced
    failure inside the audit-event write never reaches session.commit()
    — the run's own state change must not survive either (BACKEND-06 §28)."""
    organization = OrganizationRepository(db_session).create(name="Atomicity Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="Atomicity WS")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="Atomicity Campaign")
    run = CampaignRunRepository(db_session).create(campaign=campaign, run_number=1)
    db_session.flush()
    RunStageExecutionRepository(db_session).materialize_for_run(campaign_run=run)
    db_session.commit()

    service = OrchestrationService(db_session)
    with patch(
        "app.audit.repository.AuditEventRepository.record",
        side_effect=RuntimeError("simulated audit failure before commit"),
    ):
        with pytest.raises(RuntimeError, match="simulated audit failure before commit"):
            service.start_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    db_session.rollback()
    db_session.refresh(run)
    assert run.status is CampaignRunStatus.CREATED, "run must not have transitioned if its event failed to persist"


# --- BACKEND-06R: decision-event attribution ---------------------------


def _setup_running_run(db_session):
    from app.users.repository import UserRepository

    organization = OrganizationRepository(db_session).create(name="Attribution Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="Attribution WS")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="Attribution Campaign")
    run = CampaignRunRepository(db_session).create(campaign=campaign, run_number=1)
    responder = UserRepository(db_session).create(
        email="attribution-test@example.com",
        normalized_email="attribution-test@example.com",
        password_hash="not-a-real-hash",
        display_name="Attribution Test",
    )
    db_session.flush()
    service = OrchestrationService(db_session)
    service._transition_run(
        run=run, target=CampaignRunStatus.RUNNING, campaign_id=campaign.id, actor_user_id=responder.id, request_id=None
    )
    db_session.commit()
    return campaign, run, service, responder.id


def test_decision_events_are_attributable_to_the_specific_decision_request(db_session) -> None:
    """Two sequential decisions on the same run must be reconstructible
    from the stored events alone — each decision's open/resolve events
    must carry that exact decision's id, not be distinguishable only by
    timestamp ordering (BACKEND-06R required invariant)."""
    campaign, run, service, responder_id = _setup_running_run(db_session)

    request_a = service.create_decision_request(
        campaign=campaign, run=run, question="A?", stage_execution_id=None, actor_user_id=responder_id, request_id=None
    )
    service.respond_to_decision(
        campaign=campaign,
        run=run,
        decision_public_id=request_a.public_id,
        responder_user_id=responder_id,
        response_text="answer A",
        request_id=None,
    )
    request_b = service.create_decision_request(
        campaign=campaign, run=run, question="B?", stage_execution_id=None, actor_user_id=responder_id, request_id=None
    )
    service.respond_to_decision(
        campaign=campaign,
        run=run,
        decision_public_id=request_b.public_id,
        responder_user_id=responder_id,
        response_text="answer B",
        request_id=None,
    )

    assert request_a.id != request_b.id

    events_for_a = db_session.execute(
        select(AuditEvent).where(AuditEvent.decision_request_id == request_a.id)
    ).scalars().all()
    events_for_b = db_session.execute(
        select(AuditEvent).where(AuditEvent.decision_request_id == request_b.id)
    ).scalars().all()

    assert {e.event_type for e in events_for_a} == {"orchestration.decision.opened", "orchestration.decision.resolved"}
    assert {e.event_type for e in events_for_b} == {"orchestration.decision.opened", "orchestration.decision.resolved"}
    # Disjoint by construction (different decision_request_id), but assert
    # it explicitly: no event belongs to both.
    assert {e.id for e in events_for_a}.isdisjoint({e.id for e in events_for_b})


def test_decision_id_is_exposed_in_the_public_events_api(campaign_run_client: dict, db_session) -> None:
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    from app.persistence.session import get_engine
    from sqlalchemy.orm import Session as OrmSession

    engine = get_engine()
    with OrmSession(bind=engine) as session:
        from app.campaigns.repository import CampaignRepository as _CR, CampaignRunRepository as _CRR

        campaign = _CR(session).get_by_public_id(campaign_run_client["campaign_id"])
        run = _CRR(session).get_by_public_id(campaign_run_client["run_id"])
        request = OrchestrationService(session).create_decision_request(
            campaign=campaign, run=run, question="?", stage_execution_id=None, actor_user_id=None, request_id=None
        )
        decision_public_id = request.public_id

    # MVP-05E extends the deterministic bootstrap through CONTENT, so a
    # full RESEARCH->CONTENT run now produces more than the default
    # page-size (20) of run-scoped events before this decision is even
    # opened; the default page's own size is what this test used to rely
    # on, not the semantics under test. Request the maximum page instead
    # of asserting deterministic-bootstrap event volume here.
    events = campaign_run_client["client"].get(run_path(campaign_run_client, "/events?limit=100")).json()["items"]
    decision_event = next(e for e in events if e["event_type"] == "orchestration.decision.opened")
    assert decision_event["decision_id"] == decision_public_id

    run_event = next(e for e in events if e["event_type"] == "orchestration.run.transitioned")
    assert run_event["decision_id"] is None


def test_second_event_failure_in_start_run_rolls_back_both_transitions_and_both_events(db_session) -> None:
    """`start_run` performs two entity transitions (run, then stage #1),
    each with its own event. Forces the *second* event write to fail —
    proving that the first transition's already-successfully-flushed
    event is also rolled back, not just the second, unpersisted one."""
    organization = OrganizationRepository(db_session).create(name="Second Event Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="Second Event WS")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="Second Event Campaign")
    run = CampaignRunRepository(db_session).create(campaign=campaign, run_number=1)
    db_session.flush()
    RunStageExecutionRepository(db_session).materialize_for_run(campaign_run=run)
    db_session.commit()

    service = OrchestrationService(db_session)
    original_record = AuditEventRepository.record
    call_count = {"n": 0}

    def flaky_record(self, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated failure on the second event")
        return original_record(self, **kwargs)

    with patch.object(AuditEventRepository, "record", flaky_record):
        with pytest.raises(RuntimeError, match="simulated failure on the second event"):
            service.start_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    db_session.rollback()
    db_session.refresh(run)
    assert run.status is CampaignRunStatus.CREATED, "the run transition (event #1) must also roll back"

    stages = RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)
    assert stages[0].status is StageExecutionStatus.PENDING, "the stage transition must not have persisted"

    remaining_events = db_session.execute(
        select(AuditEvent).where(AuditEvent.campaign_run_id == run.id)
    ).scalars().all()
    assert remaining_events == [], (
        "the first event (run.transitioned), though successfully flushed before the "
        "second event failed, must not survive the rollback either"
    )
