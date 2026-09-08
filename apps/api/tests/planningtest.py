"""Shared helpers/fixtures for planning tests — real database required. No
public write endpoint exists in BACKEND-09 (§18), so most tests exercise
``PlanningService`` directly against real domain objects, the same
"construct a controlled fixture" pattern already used throughout
``tests/researchtest.py``/``tests/strategytest.py``.
"""

from __future__ import annotations

import pytest

from tests.researchtest import build_campaign_run_with_stages


def default_plan_item(**overrides: object) -> dict:
    payload = {
        "format": "Reel",
        "objective": "Generate awareness of the beginner-friendly training method.",
        "sequence": 1,
        "scheduled_date": None,
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def planning_campaign(db_session):
    campaign, run, stages = build_campaign_run_with_stages(db_session, campaign_name="Planning Campaign")
    return campaign, run, stages
