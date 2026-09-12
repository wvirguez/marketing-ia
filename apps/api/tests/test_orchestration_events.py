"""Append-only Audit Event traceability (BACKEND-06 §20/§21/§28).
All marked `postgres`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.campaigns.models import CampaignRunStatus
from app.campaigns.repository import CampaignBriefRepository, CampaignRepository, CampaignRunRepository
from app.orchestration.models import HumanDecisionRequest, StageExecutionStatus
from app.orchestration.repository import RunStageExecutionRepository
from app.orchestration.service import OrchestrationService
from app.research.models import ResearchReport
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


# --- MVP-06E: bootstrap lifecycle envelope events -----------------------


def _build_campaign_run_with_brief(session, *, org_name: str, workspace_name: str, campaign_name: str):
    organization = OrganizationRepository(session).create(name=org_name)
    workspace = WorkspaceRepository(session).create(organization_id=organization.id, name=workspace_name)
    campaign = CampaignRepository(session).create(workspace_id=workspace.id, name=campaign_name)
    run = CampaignRunRepository(session).create(campaign=campaign, run_number=1)
    session.flush()
    CampaignBriefRepository(session).create(
        campaign_id=campaign.id, version=1, prompt="Idea de prueba para MVP-06E.",
        product_type="Curso online", price=None, audience="Principiantes", budget=None, channel="Instagram",
    )
    session.commit()
    return campaign, run


def test_successful_bootstrap_emits_exactly_one_started_and_one_completed_event(
    campaign_run_client: dict, db_session
) -> None:
    """MVP-06E: a full RESEARCH->CONTENT success must record exactly one
    `orchestration.bootstrap.started` and one `orchestration.bootstrap.
    completed`, zero `orchestration.bootstrap.failed` — attributed to the
    real authenticated USER (never SYSTEM, which stays reserved for the
    domain content the bootstrap itself produces), correctly scoped to
    this run, and without altering CampaignRun/stage semantics."""
    initialize_run(campaign_run_client)
    start_run(campaign_run_client)

    events = campaign_run_client["client"].get(run_path(campaign_run_client, "/events?limit=100")).json()["items"]
    started = [e for e in events if e["event_type"] == "orchestration.bootstrap.started"]
    completed = [e for e in events if e["event_type"] == "orchestration.bootstrap.completed"]
    failed = [e for e in events if e["event_type"] == "orchestration.bootstrap.failed"]

    assert len(started) == 1
    assert len(completed) == 1
    assert len(failed) == 0
    assert completed[0]["new_state"] == "CONTENT"
    for event in (started[0], completed[0]):
        assert event["actor_type"] == "USER"
        assert event["actor_user_id"] is not None
        assert event["actor_user_id"].startswith("USR-")

    run = CampaignRunRepository(db_session).get_by_public_id(campaign_run_client["run_id"])
    assert run.status is CampaignRunStatus.RUNNING

    stages = {
        s.stage.value: s.status
        for s in RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)
    }
    assert stages["CONTENT"] is StageExecutionStatus.COMPLETED
    assert stages["CREATIVE"] is StageExecutionStatus.PENDING


def test_mid_bootstrap_failure_emits_started_and_failed_but_not_completed(db_session) -> None:
    """MVP-06E: a controlled AUDIENCE-stage failure (the same failure
    already exercised in test_orchestration_bootstrap.py) must still
    produce exactly one bootstrap.started and one bootstrap.failed —
    attributed to the AUDIENCE stage specifically — and zero
    bootstrap.completed. RESEARCH's own persisted output and
    CampaignRun.status must remain exactly as today (unchanged failure
    semantics)."""
    campaign, run = _build_campaign_run_with_brief(
        db_session,
        org_name="MVP-06E Failure Org",
        workspace_name="MVP-06E Failure WS",
        campaign_name="MVP-06E Failure Campaign",
    )

    service = OrchestrationService(db_session)
    service.initialize_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)
    service.start_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    with patch(
        "app.research.service.ResearchService.record_audience_profile",
        side_effect=RuntimeError("simulated audience failure"),
    ):
        with pytest.raises(RuntimeError, match="simulated audience failure"):
            service.run_deterministic_bootstrap(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    db_session.rollback()
    db_session.refresh(run)
    assert run.status is CampaignRunStatus.RUNNING

    stages = {
        s.stage.value: s
        for s in RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)
    }
    assert stages["RESEARCH"].status is StageExecutionStatus.COMPLETED
    assert stages["AUDIENCE"].status is StageExecutionStatus.FAILED

    events = db_session.execute(select(AuditEvent).where(AuditEvent.campaign_run_id == run.id)).scalars().all()
    started = [e for e in events if e.event_type == "orchestration.bootstrap.started"]
    completed = [e for e in events if e.event_type == "orchestration.bootstrap.completed"]
    failed = [e for e in events if e.event_type == "orchestration.bootstrap.failed"]

    assert len(started) == 1
    assert len(completed) == 0
    assert len(failed) == 1
    assert failed[0].stage_execution_id == stages["AUDIENCE"].id
    assert failed[0].new_state == "AUDIENCE"
    assert failed[0].actor_type is ActorType.USER

    assert (
        db_session.execute(
            select(func.count()).select_from(ResearchReport).where(ResearchReport.campaign_id == campaign.id)
        ).scalar_one()
        == 1
    )


def test_bootstrap_failed_is_recorded_even_when_the_stage_never_reached_running(db_session) -> None:
    """MVP-06E critical correction: `orchestration.bootstrap.failed` must
    never be conditioned on `stage_execution.status is RUNNING` — only the
    stage's OWN RUNNING->FAILED transition is. Forces the very first
    bootstrap stage's own READY->RUNNING transition event write to fail
    (the same flaky-record-by-call-count seam already proven safe in
    test_second_event_failure_in_start_run_rolls_back_both_transitions_and_both_events
    above), so after rollback the stage reverts to READY — never RUNNING
    — and proves bootstrap.failed still exists exactly once,
    bootstrap.started still persists, bootstrap.completed does not exist,
    and the original exception still propagates unmodified."""
    campaign, run = _build_campaign_run_with_brief(
        db_session,
        org_name="MVP-06E Pre-Running Org",
        workspace_name="MVP-06E Pre-Running WS",
        campaign_name="MVP-06E Pre-Running Campaign",
    )

    service = OrchestrationService(db_session)
    service.initialize_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)
    service.start_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    original_record = AuditEventRepository.record
    call_count = {"n": 0}

    def flaky_record(self, **kwargs):
        call_count["n"] += 1
        # Call #1 is this bootstrap's own `bootstrap.started` write (must
        # succeed); call #2 is RESEARCH's READY->RUNNING transition event
        # — failing exactly there means the exception strikes before
        # RESEARCH ever reaches RUNNING, and before its own domain writer
        # ever runs.
        if call_count["n"] == 2:
            raise RuntimeError("simulated failure before RUNNING")
        return original_record(self, **kwargs)

    with patch.object(AuditEventRepository, "record", flaky_record):
        with pytest.raises(RuntimeError, match="simulated failure before RUNNING"):
            service.run_deterministic_bootstrap(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    db_session.rollback()
    stages = {
        s.stage.value: s
        for s in RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)
    }
    assert stages["RESEARCH"].status is StageExecutionStatus.READY, (
        "the exception must have struck before RESEARCH's RUNNING transition committed"
    )

    events = db_session.execute(select(AuditEvent).where(AuditEvent.campaign_run_id == run.id)).scalars().all()
    started = [e for e in events if e.event_type == "orchestration.bootstrap.started"]
    completed = [e for e in events if e.event_type == "orchestration.bootstrap.completed"]
    failed = [e for e in events if e.event_type == "orchestration.bootstrap.failed"]

    assert len(started) == 1
    assert len(completed) == 0
    assert len(failed) == 1
    assert failed[0].stage_execution_id == stages["RESEARCH"].id
    assert failed[0].new_state == "RESEARCH"


def test_bootstrap_completed_is_atomic_with_content_completion(db_session) -> None:
    """MVP-06E-R1: `orchestration.bootstrap.completed` must be written in
    the SAME transaction/commit as CONTENT's own COMPLETED transition —
    never a separate, later transaction. Forces the audit write for
    `orchestration.bootstrap.completed` specifically (and only that
    event_type) to fail, proving: the original exception propagates;
    bootstrap.completed never persists; CONTENT does not durably remain
    COMPLETED; and the existing bootstrap-failure handling applies
    naturally (no special-casing to keep CONTENT completed without its
    envelope event)."""
    campaign, run = _build_campaign_run_with_brief(
        db_session,
        org_name="MVP-06E-R1 Atomicity Org",
        workspace_name="MVP-06E-R1 Atomicity WS",
        campaign_name="MVP-06E-R1 Atomicity Campaign",
    )

    service = OrchestrationService(db_session)
    service.initialize_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)
    service.start_run(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    original_record = AuditEventRepository.record

    def selectively_flaky_record(self, **kwargs):
        if kwargs.get("event_type") == "orchestration.bootstrap.completed":
            raise RuntimeError("simulated bootstrap.completed audit failure")
        return original_record(self, **kwargs)

    with patch.object(AuditEventRepository, "record", selectively_flaky_record):
        with pytest.raises(RuntimeError, match="simulated bootstrap.completed audit failure"):
            service.run_deterministic_bootstrap(campaign=campaign, run=run, actor_user_id=None, request_id=None)

    db_session.rollback()
    stages = {
        s.stage.value: s
        for s in RunStageExecutionRepository(db_session).list_for_run(campaign_run_id=run.id)
    }
    # Actual existing failure semantics (verified, not assumed): the
    # attempted CONTENT RUNNING->COMPLETED transition never committed, so
    # after rollback+refresh CONTENT is still RUNNING per persisted state
    # — the existing `except` block then legally transitions it
    # RUNNING->FAILED in its own recovery commit, exactly as it already
    # does for any other in-flight bootstrap exception. CONTENT never
    # remains durably COMPLETED.
    assert stages["CONTENT"].status is StageExecutionStatus.FAILED
    assert stages["CONTENT"].status is not StageExecutionStatus.COMPLETED

    events = db_session.execute(select(AuditEvent).where(AuditEvent.campaign_run_id == run.id)).scalars().all()
    started = [e for e in events if e.event_type == "orchestration.bootstrap.started"]
    completed = [e for e in events if e.event_type == "orchestration.bootstrap.completed"]
    failed = [e for e in events if e.event_type == "orchestration.bootstrap.failed"]

    assert len(started) == 1
    assert len(completed) == 0, "no false completed envelope may exist when CONTENT did not durably complete"
    assert len(failed) == 1
    assert failed[0].stage_execution_id == stages["CONTENT"].id
    assert failed[0].new_state == "CONTENT"
