"""Audit attribution and atomicity for Tracking persistence (BACKEND-15
Governance Freeze §T/§U/§V). All marked `postgres`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.tracking.models import TrackingPlan, TrackingReadinessStatus, TrackingRequirement
from app.tracking.service import TrackingService
from tests.contenttest import make_user
from tests.trackingtest import advance_plan_to, build_tracking_plan, build_tracking_requirement

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- exact attribution ------------------------------------------------


def test_plan_recorded_event_identifies_exact_plan(db_session) -> None:
    _campaign, plan = build_tracking_plan(db_session)
    events = db_session.execute(select(AuditEvent).where(AuditEvent.event_type == "tracking.plan.recorded")).scalars().all()
    matching = [e for e in events if e.tracking_plan_id == plan.id]
    assert len(matching) == 1
    assert matching[0].tracking_requirement_id is None


def test_plan_status_changed_event_carries_previous_and_new_state(db_session) -> None:
    campaign, plan = build_tracking_plan(db_session)
    reviewer = make_user(db_session)
    TrackingService(db_session).record_tracking_requirement(tracking_plan=plan, name="Purchase event")
    TrackingService(db_session).transition_tracking_plan(
        campaign=campaign, target_status=TrackingReadinessStatus.REQUIREMENTS_DEFINED, actor_user_id=reviewer.id
    )
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "tracking.plan.status_changed", AuditEvent.tracking_plan_id == plan.id)
    ).scalars().all()
    assert len(events) == 1
    assert events[0].previous_state == "NOT_DEFINED"
    assert events[0].new_state == "REQUIREMENTS_DEFINED"
    assert events[0].tracking_requirement_id is None


def test_requirement_recorded_event_identifies_both_plan_and_requirement(db_session) -> None:
    _campaign, plan, requirement = build_tracking_requirement(db_session)
    events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "tracking.requirement.recorded", AuditEvent.tracking_requirement_id == requirement.id
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].tracking_plan_id == plan.id


def test_requirement_status_changed_event_identifies_both_plan_and_requirement(db_session) -> None:
    campaign, plan, requirement = build_tracking_requirement(db_session)
    reviewer = make_user(db_session)
    TrackingService(db_session).update_tracking_requirement_status(
        campaign=campaign, requirement_public_id=requirement.public_id, status="Configurado", actor_user_id=reviewer.id
    )
    events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "tracking.requirement.status_changed",
            AuditEvent.tracking_requirement_id == requirement.id,
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].tracking_plan_id == plan.id
    assert events[0].actor_user_id == reviewer.id


def test_two_requirements_recorded_close_together_are_never_confused(db_session) -> None:
    _campaign, plan = build_tracking_plan(db_session)
    service = TrackingService(db_session)
    a = service.record_tracking_requirement(tracking_plan=plan, name="A")
    b = service.record_tracking_requirement(tracking_plan=plan, name="B")

    events = db_session.execute(select(AuditEvent).where(AuditEvent.event_type == "tracking.requirement.recorded")).scalars().all()
    matching_a = [e for e in events if e.tracking_requirement_id == a.id]
    matching_b = [e for e in events if e.tracking_requirement_id == b.id]
    assert len(matching_a) == 1
    assert len(matching_b) == 1
    assert matching_a[0].id != matching_b[0].id


# --- actor / request attribution ----------------------------------------


def test_internal_creation_defaults_to_system_actor(db_session) -> None:
    _campaign, plan, requirement = build_tracking_requirement(db_session)
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "tracking.requirement.recorded", AuditEvent.tracking_requirement_id == requirement.id)
    ).scalars().all()
    assert events[0].actor_type is ActorType.SYSTEM
    assert events[0].actor_user_id is None


def test_internal_creation_uses_user_actor_when_supplied(db_session) -> None:
    _campaign, plan = build_tracking_plan(db_session)
    reviewer = make_user(db_session)
    requirement = TrackingService(db_session).record_tracking_requirement(
        tracking_plan=plan, name="Purchase event", actor_user_id=reviewer.id
    )
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "tracking.requirement.recorded", AuditEvent.tracking_requirement_id == requirement.id)
    ).scalars().all()
    assert events[0].actor_type is ActorType.USER
    assert events[0].actor_user_id == reviewer.id


def test_patch_facing_command_always_carries_real_user_actor(db_session) -> None:
    campaign, plan = build_tracking_plan(db_session)
    reviewer = make_user(db_session)
    TrackingService(db_session).record_tracking_requirement(tracking_plan=plan, name="Purchase event")
    TrackingService(db_session).transition_tracking_plan(
        campaign=campaign, target_status=TrackingReadinessStatus.REQUIREMENTS_DEFINED, actor_user_id=reviewer.id
    )
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "tracking.plan.status_changed", AuditEvent.tracking_plan_id == plan.id)
    ).scalars().all()
    assert events[0].actor_type is ActorType.USER
    assert events[0].actor_user_id == reviewer.id


def test_request_id_is_preserved_when_supplied(db_session) -> None:
    _campaign, plan = build_tracking_plan(db_session)
    requirement = TrackingService(db_session).record_tracking_requirement(
        tracking_plan=plan, name="Purchase event", request_id="req-tracking-1"
    )
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "tracking.requirement.recorded", AuditEvent.tracking_requirement_id == requirement.id)
    ).scalars().all()
    assert events[0].request_id == "req-tracking-1"


# --- atomicity: rollback on failure --------------------------------------


def test_audit_failure_rolls_back_the_plan(db_session) -> None:
    from tests.trackingtest import build_campaign

    campaign = build_campaign(db_session)
    plans_before = _total_count(db_session, TrackingPlan)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            TrackingService(db_session).record_tracking_plan(campaign=campaign)

    db_session.rollback()
    assert _total_count(db_session, TrackingPlan) == plans_before


def test_audit_failure_rolls_back_the_requirement(db_session) -> None:
    _campaign, plan = build_tracking_plan(db_session)
    requirements_before = _total_count(db_session, TrackingRequirement)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            TrackingService(db_session).record_tracking_requirement(tracking_plan=plan, name="Purchase event")

    db_session.rollback()
    assert _total_count(db_session, TrackingRequirement) == requirements_before


def test_audit_failure_rolls_back_a_plan_transition(db_session) -> None:
    campaign, plan = build_tracking_plan(db_session)
    reviewer = make_user(db_session)
    TrackingService(db_session).record_tracking_requirement(tracking_plan=plan, name="Purchase event")

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            TrackingService(db_session).transition_tracking_plan(
                campaign=campaign, target_status=TrackingReadinessStatus.REQUIREMENTS_DEFINED, actor_user_id=reviewer.id
            )

    db_session.rollback()
    db_session.refresh(plan)
    assert plan.status is TrackingReadinessStatus.NOT_DEFINED


def test_audit_failure_rolls_back_a_requirement_status_update(db_session) -> None:
    campaign, plan, requirement = build_tracking_requirement(db_session)
    reviewer = make_user(db_session)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            TrackingService(db_session).update_tracking_requirement_status(
                campaign=campaign, requirement_public_id=requirement.public_id, status="Configurado", actor_user_id=reviewer.id
            )

    db_session.rollback()
    db_session.refresh(requirement)
    assert requirement.status is None
