"""Audit attribution and atomicity for Governed Experiment creation
(MVP-32B, frozen MVP-32A/-32A-R1 contract). All marked `postgres`."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.strategy.models import Experiment
from app.strategy.service import EVENT_EXPERIMENT_RECORDED, EVENT_HYPOTHESIS_RECORDED, StrategyService
from tests.strategytest import build_current_hypothesis

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- exact attribution -------------------------------------------------------


def test_recorded_event_identifies_the_exact_experiment_and_actor(db_session) -> None:
    campaign, strategy, hypothesis, actor = build_current_hypothesis(db_session)

    experiment = StrategyService(db_session).create_experiment(
        campaign=campaign, hypothesis_public_id=hypothesis.public_id, description="x", actor_user_id=actor.id,
    )

    event = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == EVENT_EXPERIMENT_RECORDED, AuditEvent.experiment_id == experiment.id
        )
    )
    assert event is not None
    assert event.workspace_id == hypothesis.workspace_id
    assert event.campaign_id == campaign.id
    assert event.strategy_id == strategy.id
    assert event.hypothesis_id == hypothesis.id
    assert event.actor_type == ActorType.USER
    assert event.actor_user_id == actor.id


def test_bootstrap_and_governed_creation_share_the_same_event_namespace(db_session) -> None:
    """MVP-32A §V/MVP-32A-R1: no split event namespace — bootstrap-created
    and governed-created Experiments are the exact same domain entity, so
    both record EVENT_EXPERIMENT_RECORDED. Production bootstrap currently
    never actually creates one (app/orchestration/bootstrap.py always
    synthesizes zero experiments), but the same event constant is proven
    reused here via the direct record_strategy path (mirrors the
    equivalent MVP-31B test for Hypothesis exactly)."""
    from app.orchestration.models import BusinessStage
    from tests.researchtest import build_campaign_run_with_stages
    from tests.strategytest import default_experiment, default_hypothesis

    campaign, run, stages = build_campaign_run_with_stages(db_session, campaign_name="Audit Namespace Campaign")
    StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.",
        hypotheses=[default_hypothesis(experiments=[default_experiment()])],
    )
    hypothesis_event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_HYPOTHESIS_RECORDED, AuditEvent.campaign_id == campaign.id)
    )
    experiment_event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == EVENT_EXPERIMENT_RECORDED, AuditEvent.campaign_id == campaign.id)
    )
    assert hypothesis_event is not None
    assert experiment_event is not None  # same event_type as governed creation, no separate namespace


def test_actor_user_id_is_mandatory_never_system(db_session) -> None:
    """MVP-32A §S: the governed HTTP-reachable path always has a real
    authenticated human — no default."""
    import inspect

    params = inspect.signature(StrategyService.create_experiment).parameters
    assert params["actor_user_id"].default is inspect.Parameter.empty


# --- atomicity: no partially-committed successful creation -------------------


def test_no_partial_experiment_survives_a_mid_record_failure(db_session) -> None:
    campaign, _strategy, hypothesis, actor = build_current_hypothesis(db_session)
    service = StrategyService(db_session)

    events_before = _total_count(db_session, AuditEvent)
    experiments_before = _total_count(db_session, Experiment)

    def _fail(self, *args, **kwargs):
        raise RuntimeError("simulated mid-record audit failure")

    with patch.object(AuditEventRepository, "record", _fail):
        with pytest.raises(RuntimeError, match="simulated mid-record audit failure"):
            service.create_experiment(
                campaign=campaign, hypothesis_public_id=hypothesis.public_id,
                description="should not persist", actor_user_id=actor.id,
            )

    db_session.rollback()

    assert _total_count(db_session, AuditEvent) == events_before
    assert _total_count(db_session, Experiment) == experiments_before
