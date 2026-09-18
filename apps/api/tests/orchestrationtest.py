"""Shared helpers/fixtures for orchestration tests — real database
required."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.learning.models import StrategicRecommendationDecision
from app.learning.service import LearningService
from app.orchestration.models import StrategicApprovalOutcome, StrategicDecisionType
from app.orchestration.service import StrategicApprovalService, StrategicDecisionService
from tests.campaignstest import campaign_payload, register_and_get_csrf
from tests.contenttest import make_user
from tests.learningtest import build_recommendation


@pytest.fixture()
def campaign_run_client(auth_client: TestClient) -> Iterator[dict]:
    """Registers a fresh user, creates a campaign (with its atomic
    Run #1), and returns everything an orchestration test needs:
    ``{"client", "csrf_token", "campaign_id", "run_id"}``."""
    csrf_token = register_and_get_csrf(auth_client)
    body = auth_client.post(
        "/api/v1/campaigns", json=campaign_payload(), headers={"X-CSRF-Token": csrf_token}
    ).json()
    yield {
        "client": auth_client,
        "csrf_token": csrf_token,
        "campaign_id": body["campaign"]["id"],
        "run_id": body["run"]["id"],
    }


def run_path(fixtures: dict, suffix: str = "") -> str:
    return f"/api/v1/campaigns/{fixtures['campaign_id']}/runs/{fixtures['run_id']}{suffix}"


def initialize_run(fixtures: dict) -> dict:
    response = fixtures["client"].post(run_path(fixtures, "/initialize"), headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert response.status_code == 200, response.text
    return response.json()


def start_run(fixtures: dict) -> dict:
    response = fixtures["client"].post(run_path(fixtures, "/start"), headers={"X-CSRF-Token": fixtures["csrf_token"]})
    assert response.status_code == 200, response.text
    return response.json()


def build_accepted_recommendation(session, **overrides: object):
    """MVP-28B: the sole implemented origin for a StrategicDecision (Model
    C) — a StrategicRecommendationCandidate whose own ``decision`` is
    already ACCEPTED. Builds on ``tests/learningtest.py::build_recommendation``
    (which itself builds the full Learning/Implication ancestry) and drives
    it to ACCEPTED via the real, production ``LearningService`` — no
    production bypass. Returns ``(campaign, recommendation, actor)``."""
    campaign, _analysis_result, _candidate, recommendation = build_recommendation(session, **overrides)
    actor = make_user(session)
    session.flush()
    recommendation = LearningService(session).decide_strategic_recommendation_candidate(
        campaign=campaign,
        recommendation_public_id=recommendation.public_id,
        decision=StrategicRecommendationDecision.ACCEPTED,
        actor_user_id=actor.id,
    )
    return campaign, recommendation, actor


def build_strategic_decision(
    session,
    *,
    decision_type=StrategicDecisionType.ADOPT,
    statement="Adopt shorter hooks across the campaign.",
    **overrides: object,
):
    """Builds one accepted Recommendation and records a current
    StrategicDecision for it via the real, production
    ``StrategicDecisionService``. Returns ``(campaign, recommendation,
    decision, actor)``."""
    campaign, recommendation, actor = build_accepted_recommendation(session, **overrides)
    decision = StrategicDecisionService(session).record_decision(
        campaign=campaign,
        recommendation_public_id=recommendation.public_id,
        decision_type=decision_type,
        statement=statement,
        actor_user_id=actor.id,
    )
    return campaign, recommendation, decision, actor


def build_strategic_approval(
    session,
    *,
    outcome=StrategicApprovalOutcome.APPROVED,
    **overrides: object,
):
    """MVP-29B: builds one ADOPT StrategicDecision (via
    ``build_strategic_decision`` above) and records a StrategicApproval
    for it via the real, production ``StrategicApprovalService``. Returns
    ``(campaign, recommendation, decision, approval, actor)``."""
    campaign, recommendation, decision, actor = build_strategic_decision(
        session, decision_type=StrategicDecisionType.ADOPT, **overrides
    )
    approval = StrategicApprovalService(session).record_approval(
        campaign=campaign,
        decision_public_id=decision.public_id,
        outcome=outcome,
        actor_user_id=actor.id,
    )
    return campaign, recommendation, decision, approval, actor
