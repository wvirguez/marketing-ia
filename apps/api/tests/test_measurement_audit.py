"""Audit attribution and atomicity for Measurement persistence
(BACKEND-11 §16/§Q). All marked `postgres`.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.audit.repository import AuditEventRepository
from app.measurement.models import AnalysisResult, MetricEntry, MetricSource, MetricValue, PerformanceObservation, PerformanceSignal
from app.measurement.repository import MetricValueRepository
from app.measurement.service import MeasurementService
from tests.measurementtest import default_metric_values, default_period, next_client_request_id

pytestmark = pytest.mark.postgres


def _total_count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def _record_entry(session, campaign, **overrides):
    period_start, period_end = default_period()
    fields = {
        "campaign": campaign, "period_start": period_start, "period_end": period_end,
        "channel": "Instagram", "source": MetricSource.MANUAL,
        "client_request_id": next_client_request_id(), "metric_values": default_metric_values(),
    }
    fields.update(overrides)
    return MeasurementService(session).record_metric_entry(**fields)


# --- exact attribution ------------------------------------------------


def test_metric_entry_recorded_event_identifies_the_exact_entry(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    entry = _record_entry(db_session, campaign)
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "measurement.metric_entry.recorded")
    ).scalars().all()
    matching = [e for e in events if e.metric_entry_id == entry.id]
    assert len(matching) == 1


def test_correction_uses_the_correction_event_type(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    period_start, period_end = default_period()
    correction = MeasurementService(db_session).record_metric_entry(
        campaign=campaign, period_start=period_start, period_end=period_end, channel="Instagram",
        source=MetricSource.MANUAL, client_request_id=next_client_request_id(),
        metric_values=default_metric_values(), is_correction=True,
    )
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "measurement.metric_entry.correction_recorded")
    ).scalars().all()
    assert any(e.metric_entry_id == correction.id for e in events)


def test_two_entries_created_close_together_are_never_confused(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    entry_a = _record_entry(db_session, campaign, channel="Instagram")
    entry_b = _record_entry(db_session, campaign, channel="Facebook")
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "measurement.metric_entry.recorded")
    ).scalars().all()
    matching_a = [e for e in events if e.metric_entry_id == entry_a.id]
    matching_b = [e for e in events if e.metric_entry_id == entry_b.id]
    assert len(matching_a) == 1
    assert len(matching_b) == 1
    assert matching_a[0].id != matching_b[0].id


def test_observation_signal_analysis_result_events_identify_exact_rows(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    service = MeasurementService(db_session)
    entry = _record_entry(db_session, campaign)
    observation = service.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("2.3"))
    signal = service.record_signal(campaign=campaign, observations=[observation], summary="Signal.")
    result = service.record_analysis_result(campaign=campaign, signals=[signal], summary="Analysis.")

    obs_events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "measurement.observation.recorded", AuditEvent.performance_observation_id == observation.id
        )
    ).scalars().all()
    signal_events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "measurement.signal.recorded", AuditEvent.performance_signal_id == signal.id)
    ).scalars().all()
    result_events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "measurement.analysis_result.recorded", AuditEvent.analysis_result_id == result.id
        )
    ).scalars().all()
    assert len(obs_events) == 1
    assert len(signal_events) == 1
    assert len(result_events) == 1


# --- atomicity: rollback on failure --------------------------------------


def test_audit_failure_rolls_back_the_metric_entry_and_its_values(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    entries_before = _total_count(db_session, MetricEntry)
    values_before = _total_count(db_session, MetricValue)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            _record_entry(db_session, campaign)

    db_session.rollback()
    assert _total_count(db_session, MetricEntry) == entries_before
    assert _total_count(db_session, MetricValue) == values_before


def test_child_value_failure_rolls_back_the_parent_entry(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    entries_before = _total_count(db_session, MetricEntry)

    with patch.object(MetricValueRepository, "create_many", side_effect=RuntimeError("simulated value failure")):
        with pytest.raises(RuntimeError, match="simulated value failure"):
            _record_entry(db_session, campaign)

    db_session.rollback()
    assert _total_count(db_session, MetricEntry) == entries_before


def test_audit_failure_rolls_back_observation_and_associations(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    entry = _record_entry(db_session, campaign)
    observations_before = _total_count(db_session, PerformanceObservation)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            MeasurementService(db_session).record_observation(
                campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("1")
            )

    db_session.rollback()
    assert _total_count(db_session, PerformanceObservation) == observations_before


def test_audit_failure_rolls_back_signal_and_associations(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    entry = _record_entry(db_session, campaign)
    service = MeasurementService(db_session)
    observation = service.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("1"))
    signals_before = _total_count(db_session, PerformanceSignal)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            service.record_signal(campaign=campaign, observations=[observation], summary="Should not persist.")

    db_session.rollback()
    assert _total_count(db_session, PerformanceSignal) == signals_before


def test_audit_failure_rolls_back_analysis_result_and_associations(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    entry = _record_entry(db_session, campaign)
    service = MeasurementService(db_session)
    observation = service.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("1"))
    signal = service.record_signal(campaign=campaign, observations=[observation], summary="Signal.")
    results_before = _total_count(db_session, AnalysisResult)

    with patch.object(AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            service.record_analysis_result(campaign=campaign, signals=[signal], summary="Should not persist.")

    db_session.rollback()
    assert _total_count(db_session, AnalysisResult) == results_before
