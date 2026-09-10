"""Shared helpers/fixtures for Learning tests — real database required.
No public write endpoint exists for LearningCandidate in BACKEND-14, so
most tests exercise ``LearningService`` directly against real domain
objects, the same "construct a controlled fixture" pattern already used
throughout ``tests/measurementtest.py``/``tests/contenttest.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.learning.models import LearningCandidateStatus
from app.learning.service import LearningService
from app.measurement.models import MetricSource
from app.measurement.service import MeasurementService
from tests.measurementtest import default_metric_values, default_period, next_client_request_id
from tests.researchtest import build_campaign_run_with_stages


def build_analysis_result(session, *, org_name="Learning Org", workspace_name="Learning WS", campaign_name="Learning Campaign"):
    """Creates the full ancestry (Organization/Workspace/Campaign) plus one
    real MetricEntry -> PerformanceObservation -> PerformanceSignal ->
    AnalysisResult chain via ``MeasurementService`` — Learning's sole
    upstream dependency. Returns ``(campaign, analysis_result)``."""
    campaign, _run, _stages = build_campaign_run_with_stages(
        session, org_name=org_name, workspace_name=workspace_name, campaign_name=campaign_name
    )
    service = MeasurementService(session)
    period_start, period_end = default_period()
    entry = service.record_metric_entry(
        campaign=campaign, period_start=period_start, period_end=period_end, channel="Instagram",
        source=MetricSource.MANUAL, client_request_id=next_client_request_id(), metric_values=default_metric_values(),
    )
    observation = service.record_observation(
        campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("5.0")
    )
    signal = service.record_signal(campaign=campaign, observations=[observation], summary="CTR trending up.")
    analysis_result = service.record_analysis_result(campaign=campaign, signals=[signal], summary="CTR improvement is durable.")
    return campaign, analysis_result


def default_learning_summary(**overrides: object) -> str:
    return overrides.get("summary", "Shorter hooks correlate with higher completion rate.")


def build_learning_candidate(session, **overrides: object):
    campaign, analysis_result = build_analysis_result(session, **overrides)
    candidate = LearningService(session).record_learning_candidate(
        analysis_result=analysis_result, summary=default_learning_summary()
    )
    return campaign, analysis_result, candidate


def build_validated_learning_candidate(session, **overrides: object):
    """Drives a freshly-recorded LearningCandidate through the full
    canonical path to VALIDATED: CANDIDATE_IDENTIFIED -> PROVISIONAL ->
    VALIDATION_PENDING -> VALIDATED."""
    campaign, analysis_result, candidate = build_learning_candidate(session, **overrides)
    service = LearningService(session)
    service.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.PROVISIONAL)
    service.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.VALIDATION_PENDING)
    service.transition_learning_candidate(learning_candidate=candidate, target_status=LearningCandidateStatus.VALIDATED)
    return campaign, analysis_result, candidate


def build_recommendation(session, **overrides: object):
    campaign, analysis_result, candidate = build_validated_learning_candidate(session, **overrides)
    recommendation = LearningService(session).record_strategic_recommendation_candidate(
        learning_candidate=candidate, summary="Shift creative brief toward shorter hooks."
    )
    return campaign, analysis_result, candidate, recommendation


@pytest.fixture()
def learning_campaign(db_session):
    return build_campaign_run_with_stages(db_session, campaign_name="Learning Campaign")
