"""Audit attribution and atomicity for Governed Content Plan creation
(MVP-33B, frozen MVP-33A/-33A-R1 contract). All marked `postgres`."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.planning.models import ContentPlan, PlanItem
from app.planning.service import EVENT_PLAN_ITEM_RECORDED, EVENT_PLAN_RECORDED, PlanningService
from tests.contenttest import make_user
from tests.planningtest import planning_campaign  # noqa: F401
from tests.strategytest import build_current_experiment

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- exact attribution -------------------------------------------------------


def test_recorded_event_identifies_the_exact_plan_and_actor_case_g(db_session, planning_campaign) -> None:
    campaign, _run, _stages = planning_campaign
    actor = make_user(db_session)
    plan, _items = PlanningService(db_session).create_plan(
        campaign=campaign, summary="x", experiment_public_id=None, actor_user_id=actor.id,
    )
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_PLAN_RECORDED, AuditEvent.content_plan_id == plan.id)
    )
    assert event is not None
    assert event.workspace_id == campaign.workspace_id
    assert event.campaign_id == campaign.id
    assert event.experiment_id is None
    assert event.actor_type == ActorType.USER
    assert event.actor_user_id == actor.id


def test_recorded_event_identifies_the_exact_experiment_case_e(db_session) -> None:
    campaign, _strategy, _hypothesis, experiment, actor = build_current_experiment(db_session)
    plan, _items = PlanningService(db_session).create_plan(
        campaign=campaign, summary="x", experiment_public_id=experiment.public_id, actor_user_id=actor.id,
    )
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_PLAN_RECORDED, AuditEvent.content_plan_id == plan.id)
    )
    assert event is not None
    assert event.experiment_id == experiment.id


def test_plan_item_events_recorded_for_each_initial_item(db_session, planning_campaign) -> None:
    campaign, _run, _stages = planning_campaign
    actor = make_user(db_session)
    plan, items = PlanningService(db_session).create_plan(
        campaign=campaign, summary="x", experiment_public_id=None,
        items=[
            {"format": "Reel", "objective": "a", "sequence": 1, "scheduled_date": None},
            {"format": "Email", "objective": "b", "sequence": 2, "scheduled_date": None},
        ],
        actor_user_id=actor.id,
    )
    item_events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_PLAN_ITEM_RECORDED, AuditEvent.content_plan_id == plan.id)
    ).all()
    assert {e.plan_item_id for e in item_events} == {i.id for i in items}
    assert all(e.actor_type == ActorType.USER and e.actor_user_id == actor.id for e in item_events)


def test_bootstrap_and_governed_creation_share_the_same_event_namespace(db_session) -> None:
    """MVP-33A: no split event namespace — bootstrap-created and
    governed-created ContentPlans are the exact same domain entity, so
    both record EVENT_PLAN_RECORDED (mirrors the equivalent MVP-31B/-32B
    test for Hypothesis/Experiment exactly)."""
    from app.orchestration.models import BusinessStage
    from tests.researchtest import build_campaign_run_with_stages

    campaign, run, stages = build_campaign_run_with_stages(db_session, campaign_name="Audit Namespace Campaign")
    from app.strategy.service import StrategyService

    strategy, positioning, _hyps, _exps = StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.",
    )
    PlanningService(db_session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN],
        summary="Bootstrap plan.",
    )
    actor = make_user(db_session)
    PlanningService(db_session).create_plan(
        campaign=campaign, summary="Governed plan.", experiment_public_id=None, actor_user_id=actor.id,
    )
    bootstrap_event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_PLAN_RECORDED, AuditEvent.actor_type == ActorType.SYSTEM)
    )
    governed_event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_PLAN_RECORDED, AuditEvent.actor_type == ActorType.USER)
    )
    assert bootstrap_event is not None
    assert governed_event is not None  # same event_type as bootstrap, no separate namespace


def test_actor_user_id_is_mandatory_never_system_for_governed_creation(db_session) -> None:
    """MVP-33A §V: the governed HTTP-reachable path always has a real
    authenticated human — no default."""
    import inspect

    params = inspect.signature(PlanningService.create_plan).parameters
    assert params["actor_user_id"].default is inspect.Parameter.empty


def test_bootstrap_creation_still_audits_as_system(db_session, planning_campaign) -> None:
    from app.orchestration.models import BusinessStage

    campaign, run, stages = planning_campaign
    from app.strategy.service import StrategyService

    StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.",
    )
    plan, _items = PlanningService(db_session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="x",
    )
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_PLAN_RECORDED, AuditEvent.content_plan_id == plan.id)
    )
    assert event.actor_type == ActorType.SYSTEM
    assert event.actor_user_id is None


# --- atomicity: no partially-committed successful creation -------------------


def test_no_partial_plan_survives_a_mid_record_failure(db_session, planning_campaign) -> None:
    campaign, _run, _stages = planning_campaign
    actor = make_user(db_session)
    service = PlanningService(db_session)

    events_before = _total_count(db_session, AuditEvent)
    plans_before = _total_count(db_session, ContentPlan)
    items_before = _total_count(db_session, PlanItem)

    def _fail(self, *args, **kwargs):
        raise RuntimeError("simulated mid-record audit failure")

    with patch.object(AuditEventRepository, "record", _fail):
        with pytest.raises(RuntimeError, match="simulated mid-record audit failure"):
            service.create_plan(
                campaign=campaign, summary="should not persist", experiment_public_id=None,
                items=[{"format": "Reel", "objective": "a", "sequence": 1, "scheduled_date": None}],
                actor_user_id=actor.id,
            )

    db_session.rollback()

    assert _total_count(db_session, AuditEvent) == events_before
    assert _total_count(db_session, ContentPlan) == plans_before
    assert _total_count(db_session, PlanItem) == items_before
