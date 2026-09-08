"""Audit attribution and atomicity for Planning persistence (BACKEND-09
§14/§15). All marked `postgres`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.audit.repository import AuditEventRepository
from app.orchestration.models import BusinessStage
from app.planning.models import ContentPlan, PlanItem
from app.planning.repository import PlanItemRepository
from app.planning.service import PlanningService
from tests.planningtest import default_plan_item

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- exact attribution ------------------------------------------------


def test_plan_recorded_event_identifies_the_exact_plan(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    plan, _items = PlanningService(db_session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="v1",
    )
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "planning.plan.recorded")
    ).scalars().all()
    matching = [e for e in events if e.content_plan_id == plan.id]
    assert len(matching) == 1
    assert matching[0].plan_item_id is None


def test_plan_item_recorded_event_identifies_the_exact_item(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    plan, items = PlanningService(db_session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="v1",
        items=[default_plan_item(), default_plan_item(format="Story", sequence=2)],
    )
    # Scoped to this exact Content Plan — a bare `event_type` filter would
    # also pick up planning.plan_item.recorded rows genuinely, permanently
    # committed by other test modules (e.g. tests/test_planning_api.py's
    # helper, which writes through the app's own real, non-savepoint
    # session) that share this same physical test database when the full
    # suite runs together.
    events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "planning.plan_item.recorded",
            AuditEvent.content_plan_id == plan.id,
        )
    ).scalars().all()

    for item in items:
        matching = [e for e in events if e.plan_item_id == item.id]
        assert len(matching) == 1, "each Plan Item must get exactly one attributed event"
        assert matching[0].content_plan_id == plan.id

    # Different Plan Items must never collapse into the same attribution.
    assert events[0].plan_item_id != events[1].id
    assert len({e.plan_item_id for e in events}) == 2


def test_plan_item_identity_is_never_inferred_from_sequence_or_ordering(planning_campaign, db_session) -> None:
    """Two items sharing a plan/stage/run must still be distinguishable
    purely by plan_item_id — never by re-deriving identity from sequence,
    event ordering, or event insertion order."""
    campaign, run, stages = planning_campaign
    plan, items = PlanningService(db_session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="v1",
        items=[default_plan_item(sequence=5), default_plan_item(sequence=5, format="Story")],
    )
    events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "planning.plan_item.recorded",
            AuditEvent.content_plan_id == plan.id,
        )
    ).scalars().all()
    item_ids = {i.id for i in items}
    event_item_ids = {e.plan_item_id for e in events}
    assert event_item_ids == item_ids


def test_two_plan_versions_produce_distinguishable_events(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    service = PlanningService(db_session)
    plan_1, _items = service.record_plan(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="v1")
    plan_2, _items = service.record_plan(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="v2")
    events_1 = db_session.execute(select(AuditEvent).where(AuditEvent.content_plan_id == plan_1.id)).scalars().all()
    events_2 = db_session.execute(select(AuditEvent).where(AuditEvent.content_plan_id == plan_2.id)).scalars().all()
    assert len(events_1) == 1
    assert len(events_2) == 1
    assert events_1[0].id != events_2[0].id
    assert events_1[0].new_state == "v1"
    assert events_2[0].new_state == "v2"


# --- atomicity: rollback on failure --------------------------------------


def test_audit_event_failure_rolls_back_the_entire_initial_aggregate(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    service = PlanningService(db_session)
    plans_before = _total_count(db_session, ContentPlan)
    items_before = _total_count(db_session, PlanItem)

    with patch.object(
        AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure before commit")
    ):
        with pytest.raises(RuntimeError, match="simulated audit failure before commit"):
            service.record_plan(
                campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN],
                summary="should not persist", items=[default_plan_item()],
            )

    db_session.rollback()

    assert _total_count(db_session, ContentPlan) == plans_before
    assert _total_count(db_session, PlanItem) == items_before


def test_child_item_failure_rolls_back_the_parent_plan_too(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    service = PlanningService(db_session)
    plans_before = _total_count(db_session, ContentPlan)

    with patch.object(PlanItemRepository, "create_many", side_effect=RuntimeError("simulated item failure")):
        with pytest.raises(RuntimeError, match="simulated item failure"):
            service.record_plan(
                campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN],
                summary="should not persist", items=[default_plan_item()],
            )

    db_session.rollback()

    assert _total_count(db_session, ContentPlan) == plans_before


def test_no_partial_audit_set_survives_a_mid_loop_failure(planning_campaign, db_session) -> None:
    """A failure recording the *second* item's audit event must roll back
    the first item's already-flushed event too — no partial audit set may
    survive a failed initial-persistence transaction."""
    campaign, run, stages = planning_campaign
    service = PlanningService(db_session)
    events_before = _total_count(db_session, AuditEvent)
    plans_before = _total_count(db_session, ContentPlan)
    items_before = _total_count(db_session, PlanItem)

    real_record = AuditEventRepository.record
    call_count = {"n": 0}

    def _fail_on_third_call(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 3:  # plan event (1) + first item event (2) succeed, second item event (3) fails
            raise RuntimeError("simulated mid-loop audit failure")
        return real_record(self, *args, **kwargs)

    with patch.object(AuditEventRepository, "record", _fail_on_third_call):
        with pytest.raises(RuntimeError, match="simulated mid-loop audit failure"):
            service.record_plan(
                campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN],
                summary="should not persist",
                items=[default_plan_item(), default_plan_item(format="Story", sequence=2)],
            )

    db_session.rollback()

    assert _total_count(db_session, AuditEvent) == events_before
    assert _total_count(db_session, ContentPlan) == plans_before
    assert _total_count(db_session, PlanItem) == items_before
