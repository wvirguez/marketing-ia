"""Shared helpers/fixtures for Content tests — real database required. No
public write endpoint exists in BACKEND-10 (§28/§29/§30), so most tests
exercise ``ContentService`` directly against real domain objects, the same
"construct a controlled fixture" pattern already used throughout
``tests/researchtest.py``/``tests/planningtest.py``.
"""

from __future__ import annotations

import itertools

import pytest

from app.orchestration.models import BusinessStage
from app.planning.service import PlanningService
from app.users.repository import UserRepository
from tests.planningtest import default_plan_item
from tests.researchtest import build_campaign_run_with_stages

_email_counter = itertools.count()


def default_version_payload(**overrides: object) -> dict:
    payload = {
        "kind": "reel",
        "hook": "Your dog isn't ignoring you.",
        "scenes": [{"number": 1, "visual": "Owner and dog", "onScreenText": "", "narration": "..."}],
        "caption": "Try this instead.",
        "hashtags": ["#dogtraining"],
    }
    payload.update(overrides)
    return payload


def default_piece_fields(**overrides: object) -> dict:
    payload = {
        "format": "Reel",
        "objective": "Generate identification and interest.",
        "funnel_stage": "Awareness",
        "cta": "Learn the method",
        "channel": "Instagram",
    }
    payload.update(overrides)
    return payload


def make_user(session, **overrides: object):
    n = next(_email_counter)
    payload = {
        "email": f"reviewer{n}@example.com",
        "normalized_email": f"reviewer{n}@example.com",
        "password_hash": "not-a-real-hash",
        "display_name": f"Reviewer {n}",
    }
    payload.update(overrides)
    user = UserRepository(session).create(**payload)
    session.flush()
    return user


def build_plan_with_item(session, *, org_name="Content Org", workspace_name="Content WS", campaign_name="Content Campaign"):
    """Creates Organization/Workspace/Campaign/CampaignRun/RunStageExecutions,
    a Content Plan (via PlanningService), and one Plan Item within it.
    Returns ``(campaign, run, stages, content_plan, plan_item)``."""
    campaign, run, stages = build_campaign_run_with_stages(
        session, org_name=org_name, workspace_name=workspace_name, campaign_name=campaign_name
    )
    plan, items = PlanningService(session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN],
        summary="A content calendar.", items=[default_plan_item()],
    )
    return campaign, run, stages, plan, items[0]


@pytest.fixture()
def content_campaign(db_session):
    return build_plan_with_item(db_session)
