"""Measurement domain persistence, tenancy, idempotency, correction,
cardinality, and governance-boundary tests (BACKEND-11). All marked
`postgres`.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from app.campaigns.repository import CampaignRepository
from app.core.api_errors import ProvenanceMismatchError
from app.measurement.models import (
    AnalysisResult,
    AnalysisResultSignal,
    MetricEntry,
    MetricSource,
    MetricValue,
    ObservationMetricEntry,
    PerformanceObservation,
    PerformanceSignal,
    SignalObservation,
)
from app.measurement.service import MeasurementService
from app.persistence.session import get_engine
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.measurementtest import default_metric_values, default_period, next_client_request_id
from tests.researchtest import build_campaign_run_with_stages

pytestmark = pytest.mark.postgres


def _record_entry(session, campaign, **overrides):
    period_start, period_end = default_period()
    fields = {
        "campaign": campaign, "period_start": period_start, "period_end": period_end,
        "channel": "Instagram", "source": MetricSource.MANUAL,
        "client_request_id": next_client_request_id(), "metric_values": default_metric_values(),
    }
    fields.update(overrides)
    return MeasurementService(session).record_metric_entry(**fields)


# --- Metric Entry domain persistence ---------------------------------


def test_metric_entry_persists_with_correct_public_id_and_workspace(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    entry = _record_entry(db_session, campaign)
    assert entry.public_id.startswith("MET-")
    assert entry.workspace_id == campaign.workspace_id
    assert entry.campaign_id == campaign.id
    assert entry.source is MetricSource.MANUAL


def test_metric_value_children_persist(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    entry = _record_entry(db_session, campaign, metric_values=default_metric_values(spend=Decimal("12.50")))
    values = MeasurementService(db_session).values.list_for_entry(entry.id)
    by_name = {v.metric_name: v.value for v in values}
    assert by_name["impressions"] == Decimal("1000")
    assert by_name["spend"] == Decimal("12.50")


def test_metric_value_has_no_public_id_or_workspace_id() -> None:
    columns = [c.lower() for c in MetricValue.__table__.columns.keys()]
    assert "public_id" not in columns
    assert "workspace_id" not in columns


def test_at_least_one_metric_value_is_required(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    with pytest.raises(ProvenanceMismatchError):
        _record_entry(db_session, campaign, metric_values={})


def test_period_end_before_start_rejected_at_db_level(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    rogue = MetricEntry(
        public_id="MET-BADPERIODTEST", workspace_id=campaign.workspace_id, campaign_id=campaign.id,
        period_start=date(2026, 2, 1), period_end=date(2026, 1, 1), channel="Instagram",
        source=MetricSource.MANUAL, client_request_id=next_client_request_id(),
    )
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_metric_entry_workspace_mismatch_with_campaign_rejected_at_db_level(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    other_workspace = WorkspaceRepository(db_session).create(
        organization_id=OrganizationRepository(db_session).create(name="Rogue Org").id, name="Rogue WS"
    )
    db_session.flush()
    period_start, period_end = default_period()
    rogue = MetricEntry(
        public_id="MET-MISMATCHTEST", workspace_id=other_workspace.id, campaign_id=campaign.id,
        period_start=period_start, period_end=period_end, channel="Instagram",
        source=MetricSource.MANUAL, client_request_id=next_client_request_id(),
    )
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_no_unique_constraint_on_natural_key_grouping() -> None:
    """Corrections must coexist historically — no
    UNIQUE(workspace_id, campaign_id, period_start, period_end, channel)
    exists anywhere on MetricEntry."""
    constraint_columns = [
        {c.name for c in constraint.columns}
        for constraint in MetricEntry.__table__.constraints
        if hasattr(constraint, "columns") and len(constraint.columns) > 1
    ]
    natural_key = {"workspace_id", "campaign_id", "period_start", "period_end", "channel"}
    assert natural_key not in constraint_columns


def test_metric_source_enum_membership_is_exact() -> None:
    assert {s.value for s in MetricSource} == {"MANUAL", "IMPORTED", "PLATFORM"}
    assert not hasattr(MetricSource, "DERIVED")


def test_no_forbidden_fields_exist_on_metric_entry() -> None:
    forbidden = ("status", "version", "archived_at", "deleted_at", "updated_at", "agent_id")
    columns = [c.lower() for c in MetricEntry.__table__.columns.keys()]
    for term in forbidden:
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on MetricEntry"


# --- Idempotency ---------------------------------------------------------


def test_repeated_client_request_id_returns_the_same_entry(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    request_id = next_client_request_id()
    period_start, period_end = default_period()
    service = MeasurementService(db_session)
    first = service.record_metric_entry(
        campaign=campaign, period_start=period_start, period_end=period_end, channel="Instagram",
        source=MetricSource.MANUAL, client_request_id=request_id, metric_values=default_metric_values(),
    )
    second = service.record_metric_entry(
        campaign=campaign, period_start=period_start, period_end=period_end, channel="Instagram",
        source=MetricSource.MANUAL, client_request_id=request_id, metric_values=default_metric_values(),
    )
    assert first.id == second.id

    all_entries = db_session.execute(select(MetricEntry).where(MetricEntry.client_request_id == request_id)).scalars().all()
    assert len(all_entries) == 1, "a retried POST with the same client_request_id must not create a duplicate row"


def test_different_client_request_ids_create_separate_entries(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    period_start, period_end = default_period()
    service = MeasurementService(db_session)
    first = service.record_metric_entry(
        campaign=campaign, period_start=period_start, period_end=period_end, channel="Instagram",
        source=MetricSource.MANUAL, client_request_id=next_client_request_id(), metric_values=default_metric_values(),
    )
    second = service.record_metric_entry(
        campaign=campaign, period_start=period_start, period_end=period_end, channel="Instagram",
        source=MetricSource.MANUAL, client_request_id=next_client_request_id(), metric_values=default_metric_values(),
    )
    assert first.id != second.id


# --- Correction semantics -------------------------------------------------


def test_correction_creates_new_row_and_preserves_prior(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    period_start, period_end = default_period()
    service = MeasurementService(db_session)
    original = service.record_metric_entry(
        campaign=campaign, period_start=period_start, period_end=period_end, channel="Instagram",
        source=MetricSource.MANUAL, client_request_id=next_client_request_id(),
        metric_values=default_metric_values(impressions=Decimal("1000")),
    )
    correction = service.record_metric_entry(
        campaign=campaign, period_start=period_start, period_end=period_end, channel="Instagram",
        source=MetricSource.MANUAL, client_request_id=next_client_request_id(),
        metric_values=default_metric_values(impressions=Decimal("1500")), is_correction=True,
    )
    assert correction.id != original.id

    reloaded_original = db_session.execute(select(MetricEntry).where(MetricEntry.id == original.id)).scalar_one()
    assert reloaded_original.id == original.id  # untouched, still exists exactly as created

    original_values = {v.metric_name: v.value for v in service.values.list_for_entry(original.id)}
    assert original_values["impressions"] == Decimal("1000"), "the prior entry's values must never be mutated"


def test_current_entry_is_latest_by_created_at_then_id(campaign_run_client: dict) -> None:
    """Each write happens in its own genuinely separate, immediately
    committed session bound to the app's own engine — the same pattern
    ``tests/test_content_api.py``'s ``_record_content_piece`` helper
    uses — rather than two calls sharing one savepoint-isolated
    ``db_session``. A ``session.commit()`` inside that fixture only
    releases a savepoint within one already-open outer transaction, so
    it cannot exercise the real, independently-committed transaction
    shape that ``created_at DESC`` ordering is promised against."""
    fixtures = campaign_run_client
    engine = get_engine()

    def _write(**overrides: object) -> uuid.UUID:
        period_start, period_end = default_period()
        fields = {
            "period_start": period_start, "period_end": period_end, "channel": "Instagram",
            "source": MetricSource.MANUAL, "client_request_id": next_client_request_id(),
            "metric_values": default_metric_values(),
        }
        fields.update(overrides)
        with OrmSession(bind=engine) as session:
            campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
            entry = MeasurementService(session).record_metric_entry(campaign=campaign, **fields)
            return entry.id

    original_id = _write()
    correction_id = _write(is_correction=True)

    with OrmSession(bind=engine) as session:
        campaign = CampaignRepository(session).get_by_public_id(fixtures["campaign_id"])
        rows = MeasurementService(session).list_metric_entries_for_campaign(campaign_id=campaign.id)
        current_rows = [entry for entry, _values, is_current in rows if is_current]
        all_ids = {entry.id for entry, _values, _is_current in rows}
        assert len(current_rows) == 1
        assert current_rows[0].id == correction_id
        assert original_id in all_ids
        assert correction_id in all_ids


# --- Performance Observation / Signal / Analysis Result -------------------


def test_observation_persists_with_multiple_source_entries(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    service = MeasurementService(db_session)
    entry_a = _record_entry(db_session, campaign, channel="Instagram")
    entry_b = _record_entry(db_session, campaign, channel="Facebook")

    observation = service.record_observation(
        campaign=campaign, metric_entries=[entry_a, entry_b], metric_name="CTR", value=Decimal("2.3")
    )
    assert observation.public_id.startswith("OBS-")
    source_ids = set(service.get_source_metric_entry_ids(observation.id))
    assert source_ids == {entry_a.id, entry_b.id}


def test_observation_requires_at_least_one_source(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    with pytest.raises(ProvenanceMismatchError):
        MeasurementService(db_session).record_observation(campaign=campaign, metric_entries=[], metric_name="CTR", value=Decimal("0"))


def test_observation_rejects_cross_campaign_source_entry(db_session) -> None:
    campaign_a, _run_a, _stages_a = build_campaign_run_with_stages(db_session, campaign_name="Campaign A")
    campaign_b, _run_b, _stages_b = build_campaign_run_with_stages(db_session, campaign_name="Campaign B")
    entry_b = _record_entry(db_session, campaign_b)

    with pytest.raises(ProvenanceMismatchError):
        MeasurementService(db_session).record_observation(
            campaign=campaign_a, metric_entries=[entry_b], metric_name="CTR", value=Decimal("1")
        )


def test_signal_persists_with_multiple_source_observations(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    service = MeasurementService(db_session)
    entry = _record_entry(db_session, campaign)
    obs_a = service.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("2.3"))
    obs_b = service.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CPC", value=Decimal("0.45"))

    signal = service.record_signal(campaign=campaign, observations=[obs_a, obs_b], summary="CTR trending up week over week.")
    assert signal.public_id.startswith("SIG-")
    assert set(service.get_source_observation_ids(signal.id)) == {obs_a.id, obs_b.id}


def test_analysis_result_persists_with_multiple_source_signals(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    service = MeasurementService(db_session)
    entry = _record_entry(db_session, campaign)
    obs = service.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("2.3"))
    signal_a = service.record_signal(campaign=campaign, observations=[obs], summary="Signal A")
    signal_b = service.record_signal(campaign=campaign, observations=[obs], summary="Signal B")

    result = service.record_analysis_result(campaign=campaign, signals=[signal_a, signal_b], summary="Combined interpretation.")
    assert result.public_id.startswith("ANL-")
    assert set(service.get_source_signal_ids(result.id)) == {signal_a.id, signal_b.id}


def test_recording_analysis_result_does_not_mutate_source_signal(measurement_campaign, db_session) -> None:
    """PerformanceSignal is immutable — Signal<->AnalysisResult uses an
    association table specifically so no Signal row is ever written to
    again after creation."""
    campaign, _run, _stages = measurement_campaign
    service = MeasurementService(db_session)
    entry = _record_entry(db_session, campaign)
    obs = service.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("2.3"))
    signal = service.record_signal(campaign=campaign, observations=[obs], summary="Original summary.")
    signal_snapshot = (signal.summary, signal.created_at)

    service.record_analysis_result(campaign=campaign, signals=[signal], summary="Interpretation.")

    reloaded = db_session.execute(select(PerformanceSignal).where(PerformanceSignal.id == signal.id)).scalar_one()
    assert (reloaded.summary, reloaded.created_at) == signal_snapshot


def test_performance_signal_has_no_analysis_result_id_column() -> None:
    """Governance Freeze §8: Signal<->AnalysisResult must use
    analysis_result_signals, never a plain FK on PerformanceSignal."""
    columns = [c.lower() for c in PerformanceSignal.__table__.columns.keys()]
    assert "analysis_result_id" not in columns


# --- Association table tenant safety ---------------------------------


def test_cross_tenant_association_rejected_at_db_level(db_session) -> None:
    campaign_a, _run_a, _stages_a = build_campaign_run_with_stages(db_session, campaign_name="Assoc Campaign A")
    campaign_b, _run_b, _stages_b = build_campaign_run_with_stages(db_session, campaign_name="Assoc Campaign B")
    entry_a = _record_entry(db_session, campaign_a)
    service = MeasurementService(db_session)
    obs_b = service.record_observation(
        campaign=campaign_b, metric_entries=[_record_entry(db_session, campaign_b)], metric_name="CTR", value=Decimal("1")
    )

    # Bypass the service entirely: direct model construction proves the
    # composite FK itself rejects a workspace mismatch between the two
    # sides of the association.
    rogue = ObservationMetricEntry(workspace_id=campaign_a.workspace_id, observation_id=obs_b.id, metric_entry_id=entry_a.id)
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_duplicate_association_rejected_at_db_level(measurement_campaign, db_session) -> None:
    campaign, _run, _stages = measurement_campaign
    service = MeasurementService(db_session)
    entry = _record_entry(db_session, campaign)
    observation = service.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("1"))

    rogue = ObservationMetricEntry(workspace_id=campaign.workspace_id, observation_id=observation.id, metric_entry_id=entry.id)
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_association_table_has_no_public_id_or_status() -> None:
    for model in (ObservationMetricEntry, SignalObservation, AnalysisResultSignal):
        columns = [c.lower() for c in model.__table__.columns.keys()]
        assert "public_id" not in columns
        assert "status" not in columns
        assert "version" not in columns


# --- governance: no forbidden entities/tables ------------------------------


def test_no_measurement_cycle_snapshot_or_approval_table_was_introduced() -> None:
    """BACKEND-14 has since authorized learning_candidates/
    strategic_recommendation_candidates (see tests/test_learning_domain.py)
    — this guard now covers only the tables that remain out of scope for
    every stage through BACKEND-14."""
    from app.persistence.base import metadata

    table_names = set(metadata.tables.keys())
    for forbidden_table in ("measurement_cycles", "performance_snapshots", "metric_approvals", "analysis_approvals"):
        assert forbidden_table not in table_names


def test_no_forbidden_fields_on_observation_signal_analysis_result() -> None:
    forbidden = ("status", "version", "approved", "approval", "confidence", "formula", "agent_id", "reasoning")
    for model in (PerformanceObservation, PerformanceSignal, AnalysisResult):
        columns = [c.lower() for c in model.__table__.columns.keys()]
        for term in forbidden:
            assert not any(term in c for c in columns), f"unexpected field containing {term!r} on {model.__name__}"


def test_analysis_result_has_no_run_or_stage_provenance() -> None:
    columns = [c.lower() for c in AnalysisResult.__table__.columns.keys()]
    assert "campaign_run_id" not in columns
    assert "stage_execution_id" not in columns


# --- stage-lifecycle / orchestration non-mutation -----------------------


def test_recording_metric_entry_leaves_run_and_stage_unchanged(measurement_campaign, db_session) -> None:
    from app.campaigns.models import CampaignRunStatus
    from app.orchestration.models import StageExecutionStatus

    campaign, run, stages = measurement_campaign
    _record_entry(db_session, campaign)
    db_session.refresh(run)
    assert run.status is CampaignRunStatus.CREATED
    for stage in stages.values():
        db_session.refresh(stage)
        assert stage.status is StageExecutionStatus.PENDING
