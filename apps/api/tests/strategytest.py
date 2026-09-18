"""Shared helpers/fixtures for strategy tests — real database required. No
public write endpoint exists in BACKEND-08 (§19), so most tests exercise
``StrategyService`` directly against real domain objects, the same
"construct a controlled fixture" pattern already used throughout
``tests/researchtest.py``/``tests/test_orchestration_*.py``.
"""

from __future__ import annotations

import pytest

from tests.researchtest import build_campaign_run_with_stages


def default_hypothesis(**overrides: object) -> dict:
    payload = {
        "statement": "First-time owners will respond better to a structured, step-by-step onboarding message.",
    }
    payload.update(overrides)
    return payload


def default_experiment(**overrides: object) -> dict:
    payload = {
        "description": "A/B test two onboarding email sequences against a held-out control group.",
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def strategy_campaign(db_session):
    campaign, run, stages = build_campaign_run_with_stages(db_session, campaign_name="Strategy Campaign")
    return campaign, run, stages


def build_current_hypothesis(session, *, campaign_name="Hypothesis Campaign", statement=None, actor=None):
    """MVP-32B: bootstraps a fresh Strategy (v1) for a brand-new campaign,
    then records a governed Hypothesis under it via the real, production
    ``StrategyService.create_hypothesis`` — the minimum ancestry Experiment
    tests need for the common (current-Strategy) case, without the full
    StrategicApproval chain ``tests/orchestrationtest.py`` builds for
    StrategyRevision tests. Returns ``(campaign, strategy, hypothesis,
    actor)``."""
    from app.orchestration.models import BusinessStage
    from app.strategy.service import StrategyService
    from tests.contenttest import make_user

    campaign, run, stages = build_campaign_run_with_stages(session, campaign_name=campaign_name)
    service = StrategyService(session)
    strategy, _positioning, _hyps, _exps = service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.",
    )
    actor = actor or make_user(session)
    session.flush()
    hypothesis = service.create_hypothesis(
        campaign=campaign, strategy_public_id=strategy.public_id,
        statement=statement or "First-time owners respond better to a structured onboarding message.",
        actor_user_id=actor.id,
    )
    return campaign, strategy, hypothesis, actor
