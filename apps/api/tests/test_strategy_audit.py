"""Audit attribution and atomicity for Strategy persistence (BACKEND-08
§17/§18). All marked `postgres`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.audit.repository import AuditEventRepository
from app.orchestration.models import BusinessStage
from app.strategy.models import Experiment, Hypothesis, HypothesisStatus, Positioning, Strategy
from app.strategy.repository import HypothesisRepository
from app.strategy.service import StrategyService
from tests.strategytest import default_experiment, default_hypothesis

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- exact attribution ------------------------------------------------


def test_strategy_recorded_event_identifies_the_exact_strategy(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    strategy, *_ = StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="v1", positioning_statement="Statement.",
    )
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "strategy.recorded")
    ).scalars().all()
    matching = [e for e in events if e.strategy_id == strategy.id]
    assert len(matching) == 1
    assert matching[0].hypothesis_id is None
    assert matching[0].experiment_id is None


def test_hypothesis_recorded_event_identifies_the_exact_hypothesis(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    _strategy, _pos, hypotheses, _exps = StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="v1", positioning_statement="Statement.",
        hypotheses=[default_hypothesis(), default_hypothesis(statement="Second one.")],
    )
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "strategy.hypothesis.recorded")
    ).scalars().all()
    for hypothesis in hypotheses:
        matching = [e for e in events if e.hypothesis_id == hypothesis.id]
        assert len(matching) == 1
    assert events[0].id != events[1].id


def test_experiment_recorded_event_identifies_the_exact_experiment(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    _strategy, _pos, hypotheses, experiments = StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="v1", positioning_statement="Statement.",
        hypotheses=[default_hypothesis(experiments=[default_experiment(), default_experiment()])],
    )
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "strategy.experiment.recorded")
    ).scalars().all()
    for experiment in experiments:
        matching = [e for e in events if e.experiment_id == experiment.id]
        assert len(matching) == 1
        assert matching[0].hypothesis_id == hypotheses[0].id


def test_two_strategy_versions_produce_distinguishable_events(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    strategy_1, *_ = service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="v1", positioning_statement="s1",
    )
    strategy_2, *_ = service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="v2", positioning_statement="s2",
    )
    events_1 = db_session.execute(select(AuditEvent).where(AuditEvent.strategy_id == strategy_1.id)).scalars().all()
    events_2 = db_session.execute(select(AuditEvent).where(AuditEvent.strategy_id == strategy_2.id)).scalars().all()
    assert len(events_1) == 1
    assert len(events_2) == 1
    assert events_1[0].id != events_2[0].id
    assert events_1[0].new_state == "v1"
    assert events_2[0].new_state == "v2"


def test_hypothesis_status_change_event_identifies_the_exact_hypothesis_and_transition(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    _strategy, _pos, hypotheses, _exps = service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="v1", positioning_statement="Statement.", hypotheses=[default_hypothesis()],
    )
    service.transition_hypothesis(
        campaign=campaign, hypothesis_public_id=hypotheses[0].public_id, target_status=HypothesisStatus.CONFIRMED,
    )
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "strategy.hypothesis.status_changed")
    ).scalars().all()
    matching = [e for e in events if e.hypothesis_id == hypotheses[0].id]
    assert len(matching) == 1
    assert matching[0].previous_state == "OPEN"
    assert matching[0].new_state == "CONFIRMED"


# --- atomicity: rollback on failure --------------------------------------


def test_audit_event_failure_rolls_back_the_entire_initial_aggregate(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    strategies_before = _total_count(db_session, Strategy)
    positionings_before = _total_count(db_session, Positioning)
    hypotheses_before = _total_count(db_session, Hypothesis)
    experiments_before = _total_count(db_session, Experiment)

    with patch.object(
        AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure before commit")
    ):
        with pytest.raises(RuntimeError, match="simulated audit failure before commit"):
            service.record_strategy(
                campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
                summary="should not persist", positioning_statement="should not persist",
                hypotheses=[default_hypothesis(experiments=[default_experiment()])],
            )

    db_session.rollback()

    assert _total_count(db_session, Strategy) == strategies_before
    assert _total_count(db_session, Positioning) == positionings_before
    assert _total_count(db_session, Hypothesis) == hypotheses_before
    assert _total_count(db_session, Experiment) == experiments_before


def test_child_hypothesis_failure_rolls_back_the_parent_strategy_and_positioning_too(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    strategies_before = _total_count(db_session, Strategy)
    positionings_before = _total_count(db_session, Positioning)

    with patch.object(HypothesisRepository, "create_many", side_effect=RuntimeError("simulated hypothesis failure")):
        with pytest.raises(RuntimeError, match="simulated hypothesis failure"):
            service.record_strategy(
                campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
                summary="should not persist", positioning_statement="should not persist",
                hypotheses=[default_hypothesis()],
            )

    db_session.rollback()

    assert _total_count(db_session, Strategy) == strategies_before
    assert _total_count(db_session, Positioning) == positionings_before


def test_audit_event_failure_rolls_back_a_hypothesis_transition(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    _strategy, _pos, hypotheses, _exps = service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="v1", positioning_statement="Statement.", hypotheses=[default_hypothesis()],
    )
    hypothesis_id = hypotheses[0].id

    with patch.object(
        AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure before commit")
    ):
        with pytest.raises(RuntimeError, match="simulated audit failure before commit"):
            service.transition_hypothesis(
                campaign=campaign, hypothesis_public_id=hypotheses[0].public_id,
                target_status=HypothesisStatus.CONFIRMED,
            )

    db_session.rollback()

    reloaded = db_session.execute(select(Hypothesis).where(Hypothesis.id == hypothesis_id)).scalar_one()
    assert reloaded.status is HypothesisStatus.OPEN, "the transition must not survive if its event failed to persist"
