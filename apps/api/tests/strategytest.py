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
