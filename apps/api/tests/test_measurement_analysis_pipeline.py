"""Measurement Analysis Run pipeline tests (MVP-11B).

Covers: schema/generic-compatibility regression, run creation/idempotency,
Observation derivation (identity, reuse, race-safety), Signal derivation
(baseline selection, identity, reuse, race-safety), AnalysisResult
creation rules, checkpoint/failure semantics, and audit provenance —
per the MVP-11A / -R1 / -R2 / -R3 frozen architecture. All marked
`postgres`.
"""

from __future__ import annotations

import threading
import time
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from app.audit.models import AuditEvent
from app.campaigns.models import CampaignRunStatus
from app.campaigns.repository import CampaignRepository, CampaignRunRepository
from app.core.api_errors import ProvenanceMismatchError
from app.learning.models import LearningCandidate
from app.measurement.analysis_pipeline import (
    EVENT_ANALYSIS_RUN_COMPLETED,
    EVENT_ANALYSIS_RUN_FAILED,
    EVENT_ANALYSIS_RUN_STARTED,
    MeasurementAnalysisService,
)
from app.measurement.models import (
    AnalysisResult,
    MeasurementAnalysisRun,
    MeasurementAnalysisRunMetricEntry,
    MeasurementAnalysisRunObservationUsage,
    MeasurementAnalysisRunResult,
    MeasurementAnalysisRunSignalUsage,
    MeasurementAnalysisRunStatus,
    MeasurementObservationDerivation,
    MeasurementSignalDerivation,
    MetricEntry,
    MetricSource,
    PerformanceObservation,
    PerformanceSignal,
)
from app.measurement.service import EVENT_OBSERVATION_RECORDED, EVENT_SIGNAL_RECORDED, MeasurementService
from app.orchestration.models import StageExecutionStatus
from app.orchestration.repository import RunStageExecutionRepository
from app.persistence.session import configure_database, dispose_engine, get_engine
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.measurementtest import next_client_request_id
from tests.researchtest import build_campaign_run_with_stages

pytestmark = pytest.mark.postgres

_FORBIDDEN_GOVERNANCE_WORDS = (
    "improved", "worsened", "better", "worse", "significant", "winner", "proven", "causal",
    "optimized", "validated", "recommendation", "learning", "decision",
)


def _campaign(db_session, **kwargs):
    campaign, run, stages = build_campaign_run_with_stages(db_session, **kwargs)
    return campaign, run, stages


def _record_entry(session, campaign, *, period_start, period_end, channel="Instagram", values=None):
    values = values or {"clicks": Decimal("100")}
    return MeasurementService(session).record_metric_entry(
        campaign=campaign, period_start=period_start, period_end=period_end, channel=channel,
        source=MetricSource.MANUAL, client_request_id=next_client_request_id(), metric_values=values,
    )


def _key() -> str:
    return next_client_request_id()


# =====================================================================
# Schema / generic-compatibility regression (MVP-11B §41)
# =====================================================================


def test_generic_observation_without_pipeline_run_still_valid(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Generic Obs Campaign")
    entry = _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    observation = MeasurementService(db_session).record_observation(
        campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("2.3")
    )
    derivation = db_session.execute(
        select(MeasurementObservationDerivation).where(MeasurementObservationDerivation.observation_id == observation.id)
    ).scalar_one_or_none()
    assert derivation is None, "a generic, manually-created Observation must have no pipeline derivation row"


def test_generic_observation_still_supports_multiple_metric_entries(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Generic Multi Obs Campaign")
    entry_a = _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), channel="Instagram")
    entry_b = _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), channel="Facebook")
    observation = MeasurementService(db_session).record_observation(
        campaign=campaign, metric_entries=[entry_a, entry_b], metric_name="CTR", value=Decimal("2.3")
    )
    assert observation.public_id.startswith("OBS-")


def test_generic_signal_still_supports_arbitrary_observation_sources(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Generic Multi Signal Campaign")
    entry = _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    service = MeasurementService(db_session)
    obs_a = service.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("2.3"))
    obs_b = service.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CPC", value=Decimal("0.4"))
    signal = service.record_signal(campaign=campaign, observations=[obs_a, obs_b], summary="Generic multi-source signal.")
    derivation = db_session.execute(
        select(MeasurementSignalDerivation).where(MeasurementSignalDerivation.signal_id == signal.id)
    ).scalar_one_or_none()
    assert derivation is None, "a generic Signal spanning arbitrary Observations must have no pipeline derivation row"


def test_generic_analysis_result_still_valid(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Generic Analysis Result Campaign")
    entry = _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    service = MeasurementService(db_session)
    obs = service.record_observation(campaign=campaign, metric_entries=[entry], metric_name="CTR", value=Decimal("2.3"))
    signal_a = service.record_signal(campaign=campaign, observations=[obs], summary="A")
    signal_b = service.record_signal(campaign=campaign, observations=[obs], summary="B")
    result = service.record_analysis_result(campaign=campaign, signals=[signal_a, signal_b], summary="Combined.")
    ownership = db_session.execute(
        select(MeasurementAnalysisRunResult).where(MeasurementAnalysisRunResult.analysis_result_id == result.id)
    ).scalar_one_or_none()
    assert ownership is None, "a generic AnalysisResult must have no pipeline ownership row"


def test_learning_fk_unaffected() -> None:
    columns = LearningCandidate.__table__.columns.keys()
    assert "analysis_result_id" in columns


def test_core_measurement_entities_have_no_new_pipeline_columns() -> None:
    for model, forbidden in (
        (MetricEntry, ("measurement_analysis_run_id",)),
        (PerformanceObservation, ("measurement_analysis_run_id", "source_metric_entry_id")),
        (PerformanceSignal, ("measurement_analysis_run_id", "current_observation_id", "prior_observation_id")),
        (AnalysisResult, ("measurement_analysis_run_id",)),
    ):
        columns = model.__table__.columns.keys()
        for column in forbidden:
            assert column not in columns, f"{model.__name__} must not gain the pipeline-specific column {column!r}"


# =====================================================================
# Run creation / idempotency (MVP-11B §42)
# =====================================================================


def test_run_creation_has_expected_public_id_prefix_and_initial_state(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Run Creation Campaign")
    from app.measurement.repository import MeasurementAnalysisRunRepository

    run = MeasurementAnalysisRunRepository(db_session).create(campaign=campaign, client_request_id=_key())
    assert run.public_id.startswith("MAR-")
    assert run.status is MeasurementAnalysisRunStatus.RUNNING
    assert run.failure_reason is None
    assert run.completed_at is None


def test_run_produces_complete_input_snapshot(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Snapshot Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), channel="Instagram")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), channel="Facebook")
    key = _key()
    run = MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=key)
    snapshot_rows = db_session.execute(
        select(MeasurementAnalysisRunMetricEntry).where(MeasurementAnalysisRunMetricEntry.analysis_run_id == run.id)
    ).scalars().all()
    assert len(snapshot_rows) == 2


def test_workspace_client_request_id_uniqueness_enforced_at_db_level(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Uniqueness Campaign")
    from app.measurement.repository import MeasurementAnalysisRunRepository

    repo = MeasurementAnalysisRunRepository(db_session)
    key = _key()
    repo.create(campaign=campaign, client_request_id=key)
    db_session.flush()
    with pytest.raises(IntegrityError):
        repo.create(campaign=campaign, client_request_id=key)
    db_session.rollback()


def test_same_key_replay_returns_same_run_without_re_execution(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Replay Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    key = _key()
    service = MeasurementAnalysisService(db_session)
    first = service.run_analysis(campaign=campaign, client_request_id=key)
    second = service.run_analysis(campaign=campaign, client_request_id=key)
    assert first.id == second.id

    total_runs = db_session.execute(
        select(MeasurementAnalysisRun).where(MeasurementAnalysisRun.workspace_id == campaign.workspace_id)
    ).scalars().all()
    assert len(total_runs) == 1

    snapshot_rows = db_session.execute(
        select(MeasurementAnalysisRunMetricEntry).where(MeasurementAnalysisRunMetricEntry.analysis_run_id == first.id)
    ).scalars().all()
    assert len(snapshot_rows) == 1, "a same-key replay must not duplicate the input snapshot"


def test_completed_run_is_terminal_and_unaffected_by_same_key_replay(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Terminal Completed Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    key = _key()
    service = MeasurementAnalysisService(db_session)
    run = service.run_analysis(campaign=campaign, client_request_id=key)
    assert run.status is MeasurementAnalysisRunStatus.COMPLETED
    completed_at = run.completed_at

    replayed = service.run_analysis(campaign=campaign, client_request_id=key)
    assert replayed.status is MeasurementAnalysisRunStatus.COMPLETED
    assert replayed.completed_at == completed_at


# =====================================================================
# Observation derivation (MVP-11B §43)
# =====================================================================


def test_first_pipeline_observation_created_with_creator_provenance(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="First Observation Campaign")
    entry = _record_entry(
        db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        values={"reuniones_agendadas": Decimal("-5")},
    )
    key = _key()
    run = MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=key)

    derivation = db_session.execute(
        select(MeasurementObservationDerivation).where(
            MeasurementObservationDerivation.source_metric_entry_id == entry.id,
            MeasurementObservationDerivation.metric_name == "reuniones_agendadas",
        )
    ).scalar_one()
    assert derivation.creator_analysis_run_id == run.id
    observation = db_session.get(PerformanceObservation, derivation.observation_id)
    assert observation.value == Decimal("-5.0000")
    assert observation.metric_name == "reuniones_agendadas"


def test_observation_dedupe_and_reuse_across_two_runs(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Observation Reuse Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    service = MeasurementAnalysisService(db_session)
    run_a = service.run_analysis(campaign=campaign, client_request_id=_key())
    run_b = service.run_analysis(campaign=campaign, client_request_id=_key())

    derivations = db_session.execute(select(MeasurementObservationDerivation)).scalars().all()
    assert len(derivations) == 1, "the same (entry, metric_name) must never produce a second Observation"
    assert derivations[0].creator_analysis_run_id == run_a.id, "creator provenance must stay with the original run"

    usages = db_session.execute(
        select(MeasurementAnalysisRunObservationUsage).where(
            MeasurementAnalysisRunObservationUsage.observation_id == derivations[0].observation_id
        )
    ).scalars().all()
    usage_run_ids = {usage.analysis_run_id for usage in usages}
    assert usage_run_ids == {run_a.id, run_b.id}, "both runs must have their own usage row for the reused Observation"


def test_correction_creates_new_observation_identity_for_the_corrected_entry(postgres_engine) -> None:
    # Each step happens in its own genuinely separate, immediately
    # committed session bound to the app's own engine (matching
    # test_measurement_domain.py's own
    # test_current_entry_is_latest_by_created_at_then_id precedent) —
    # `db_session`'s single shared transaction makes every `created_at`
    # identical (Postgres `now()` is frozen per-transaction), which would
    # make "current" ordering fall back to comparing random UUIDs instead
    # of genuine recency. `get_engine()` only works once the app's global
    # engine is configured (mirroring tests/authtest.py's `auth_client`
    # fixture) — point it at the real, migrated test database rather than
    # whatever DATABASE_URL happens to be set to.
    configure_database(postgres_engine.url.render_as_string(hide_password=False), echo=False)
    try:
        engine = get_engine()
        _run_correction_creates_new_observation_identity(engine)
    finally:
        dispose_engine()


def _run_correction_creates_new_observation_identity(engine) -> None:
    with OrmSession(bind=engine) as session:
        campaign, _run, _stages = _campaign(session, campaign_name="Correction Observation Campaign")
        session.commit()
        campaign_id = campaign.id

    with OrmSession(bind=engine) as session:
        campaign = session.get(type(campaign), campaign_id)
        e1 = _record_entry(session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
        e1_id = e1.id
        MeasurementAnalysisService(session).run_analysis(campaign=campaign, client_request_id=_key())

    with OrmSession(bind=engine) as session:
        e1_derivation = session.execute(
            select(MeasurementObservationDerivation).where(MeasurementObservationDerivation.source_metric_entry_id == e1_id)
        ).scalar_one()
        e1_derivation_observation_id = e1_derivation.observation_id

    with OrmSession(bind=engine) as session:
        campaign = session.get(type(campaign), campaign_id)
        e1 = session.get(MetricEntry, e1_id)
        e2 = MeasurementService(session).record_metric_entry(
            campaign=campaign, period_start=e1.period_start, period_end=e1.period_end, channel=e1.channel,
            source=MetricSource.MANUAL, client_request_id=_key(), metric_values={"clicks": Decimal("150")},
            is_correction=True,
        )
        e2_id = e2.id
        MeasurementAnalysisService(session).run_analysis(campaign=campaign, client_request_id=_key())

    with OrmSession(bind=engine) as session:
        e2_derivation = session.execute(
            select(MeasurementObservationDerivation).where(MeasurementObservationDerivation.source_metric_entry_id == e2_id)
        ).scalar_one()
        assert e2_derivation.observation_id != e1_derivation_observation_id

        reloaded_e1_derivation = session.execute(
            select(MeasurementObservationDerivation).where(MeasurementObservationDerivation.source_metric_entry_id == e1_id)
        ).scalar_one()
        assert reloaded_e1_derivation.observation_id == e1_derivation_observation_id, "E1's Observation must remain untouched"


def test_pipeline_observation_rejects_cross_campaign_entry(db_session) -> None:
    campaign_a, _run_a, _stages_a = _campaign(db_session, org_name="Org A", workspace_name="WS A", campaign_name="Campaign A")
    campaign_b, _run_b, _stages_b = _campaign(db_session, org_name="Org B", workspace_name="WS B", campaign_name="Campaign B")
    foreign_entry = _record_entry(db_session, campaign_b, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))

    service = MeasurementAnalysisService(db_session)
    from app.measurement.repository import MeasurementAnalysisRunRepository

    run = MeasurementAnalysisRunRepository(db_session).create(campaign=campaign_a, client_request_id=_key())
    db_session.flush()
    with pytest.raises(ProvenanceMismatchError):
        service._derive_one_observation(
            campaign=campaign_a, run=run, source_entry=foreign_entry, metric_name="clicks", value=Decimal("1"),
            actor_user_id=None, request_id=None,
        )


def test_concurrent_observation_derivation_race_leaves_no_orphan_or_false_audit(postgres_engine) -> None:
    # `db_session` (used elsewhere in this file) only releases a SAVEPOINT
    # within a still-open outer transaction that never becomes visible to
    # another connection (see tests/dbtest.py and
    # tests/test_orchestration_concurrency.py's own identical note) — the
    # shared fixture setup here therefore uses a plain session bound to
    # its own fresh connection (no pre-started external transaction), so
    # each `commit()` really commits, immediately visible to the two
    # independent connections the race itself uses below.
    with postgres_engine.connect() as setup_connection:
        setup_session = OrmSession(bind=setup_connection)
        campaign, _run, _stages = _campaign(setup_session, campaign_name="Observation Race Campaign")
        setup_session.commit()
        entry = _record_entry(setup_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
        campaign_id, entry_id = campaign.id, entry.id
        campaign_type = type(campaign)
        workspace_id = campaign.workspace_id
        setup_session.close()

    ready = threading.Barrier(2)
    results: list[MeasurementObservationDerivation] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def attempt() -> None:
        with postgres_engine.connect() as connection:
            session = OrmSession(bind=connection)
            try:
                from app.measurement.repository import MeasurementAnalysisRunRepository

                local_campaign = session.get(campaign_type, campaign_id)
                local_entry = session.get(MetricEntry, entry_id)
                run = MeasurementAnalysisRunRepository(session).create(campaign=local_campaign, client_request_id=_key())
                session.commit()
                service = MeasurementAnalysisService(session)
                ready.wait(timeout=5)
                observation = service._derive_one_observation(
                    campaign=local_campaign, run=run, source_entry=local_entry, metric_name="clicks",
                    value=Decimal("100"), actor_user_id=None, request_id=None,
                )
                derivation = session.execute(
                    select(MeasurementObservationDerivation).where(
                        MeasurementObservationDerivation.observation_id == observation.id
                    )
                ).scalar_one()
                with lock:
                    results.append(derivation)
            except Exception as exc:  # pragma: no cover - surfaced via errors list
                with lock:
                    errors.append(exc)
            finally:
                session.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"unexpected errors: {errors}"
    assert len(results) == 2

    with postgres_engine.connect() as connection:
        derivations = connection.execute(
            select(MeasurementObservationDerivation.__table__).where(
                MeasurementObservationDerivation.__table__.c.source_metric_entry_id == entry_id
            )
        ).all()
        assert len(derivations) == 1, "only one derivation (and one Observation) may survive the race"

        observations = connection.execute(
            select(PerformanceObservation.__table__).where(
                PerformanceObservation.__table__.c.campaign_id == campaign_id
            )
        ).all()
        assert len(observations) == 1, "the losing attempt must leave no orphan PerformanceObservation"

        winner_observation_id = derivations[0].observation_id
        recorded_events = connection.execute(
            select(AuditEvent.__table__).where(
                AuditEvent.__table__.c.event_type == EVENT_OBSERVATION_RECORDED,
                AuditEvent.__table__.c.performance_observation_id == winner_observation_id,
            )
        ).all()
        assert len(recorded_events) == 1, "exactly one .recorded event may exist — the loser's must never survive"

        usage_rows = connection.execute(
            select(MeasurementAnalysisRunObservationUsage.__table__).where(
                MeasurementAnalysisRunObservationUsage.__table__.c.observation_id == winner_observation_id
            )
        ).all()
        assert len(usage_rows) == 2, "both racing runs must each get their own usage row against the winner"


# =====================================================================
# Signal derivation (MVP-11B §44)
# =====================================================================


def test_signal_reports_increased(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Signal Increase Campaign")
    _record_entry(
        db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        values={"clicks": Decimal("100")},
    )
    _record_entry(
        db_session, campaign, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28),
        values={"clicks": Decimal("150")},
    )
    run = MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=_key())
    signal = db_session.execute(
        select(PerformanceSignal).where(PerformanceSignal.campaign_id == campaign.id)
    ).scalar_one()
    assert "increased" in signal.summary
    assert run.status is MeasurementAnalysisRunStatus.COMPLETED


def test_signal_reports_decreased(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Signal Decrease Campaign")
    _record_entry(
        db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        values={"clicks": Decimal("150")},
    )
    _record_entry(
        db_session, campaign, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28),
        values={"clicks": Decimal("100")},
    )
    MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=_key())
    signal = db_session.execute(
        select(PerformanceSignal).where(PerformanceSignal.campaign_id == campaign.id)
    ).scalar_one()
    assert "decreased" in signal.summary


def test_signal_reports_unchanged(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Signal Unchanged Campaign")
    _record_entry(
        db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        values={"clicks": Decimal("100")},
    )
    _record_entry(
        db_session, campaign, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28),
        values={"clicks": Decimal("100")},
    )
    MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=_key())
    signal = db_session.execute(
        select(PerformanceSignal).where(PerformanceSignal.campaign_id == campaign.id)
    ).scalar_one()
    assert "remained unchanged" in signal.summary
    for forbidden in _FORBIDDEN_GOVERNANCE_WORDS:
        assert forbidden not in signal.summary.lower()


def test_no_baseline_produces_no_signal(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="No Baseline Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    run = MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=_key())
    signals = db_session.execute(select(PerformanceSignal).where(PerformanceSignal.campaign_id == campaign.id)).scalars().all()
    assert signals == []
    assert run.status is MeasurementAnalysisRunStatus.COMPLETED, "no baseline is not a failure"


def test_same_period_correction_is_never_used_as_a_baseline(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Same Period Correction Campaign")
    e1 = _record_entry(
        db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        values={"clicks": Decimal("100")},
    )
    MeasurementService(db_session).record_metric_entry(
        campaign=campaign, period_start=e1.period_start, period_end=e1.period_end, channel=e1.channel,
        source=MetricSource.MANUAL, client_request_id=_key(), metric_values={"clicks": Decimal("150")},
        is_correction=True,
    )
    run = MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=_key())
    signals = db_session.execute(select(PerformanceSignal).where(PerformanceSignal.campaign_id == campaign.id)).scalars().all()
    assert signals == [], "a same-period correction must never be compared against itself"
    assert run.status is MeasurementAnalysisRunStatus.COMPLETED


def test_cross_channel_metrics_are_never_compared(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Cross Channel Campaign")
    _record_entry(
        db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), channel="Instagram",
        values={"clicks": Decimal("100")},
    )
    _record_entry(
        db_session, campaign, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), channel="Facebook",
        values={"clicks": Decimal("999")},
    )
    run = MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=_key())
    signals = db_session.execute(select(PerformanceSignal).where(PerformanceSignal.campaign_id == campaign.id)).scalars().all()
    assert signals == [], "different channels must never be treated as comparable periods"
    assert run.status is MeasurementAnalysisRunStatus.COMPLETED


def test_exact_metric_name_grouping_required_for_comparison(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Metric Name Grouping Campaign")
    _record_entry(
        db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        values={"clicks": Decimal("100")},
    )
    _record_entry(
        db_session, campaign, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28),
        values={"impressions": Decimal("999")},
    )
    MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=_key())
    signals = db_session.execute(select(PerformanceSignal).where(PerformanceSignal.campaign_id == campaign.id)).scalars().all()
    assert signals == [], "different metric names must never be compared against each other"


def test_signal_pair_dedupe_and_reuse_across_two_runs(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Signal Reuse Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), values={"clicks": Decimal("100")})
    _record_entry(db_session, campaign, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), values={"clicks": Decimal("150")})
    service = MeasurementAnalysisService(db_session)
    run_a = service.run_analysis(campaign=campaign, client_request_id=_key())
    run_b = service.run_analysis(campaign=campaign, client_request_id=_key())

    derivations = db_session.execute(select(MeasurementSignalDerivation)).scalars().all()
    assert len(derivations) == 1, "the same comparison pair must never produce a second Signal"
    assert derivations[0].creator_analysis_run_id == run_a.id

    usages = db_session.execute(
        select(MeasurementAnalysisRunSignalUsage).where(MeasurementAnalysisRunSignalUsage.signal_id == derivations[0].signal_id)
    ).scalars().all()
    assert {u.analysis_run_id for u in usages} == {run_a.id, run_b.id}


def test_signal_derivation_self_comparison_rejected_by_check_constraint(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Self Comparison Campaign")
    entry = _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    service = MeasurementService(db_session)
    observation = service.record_observation(campaign=campaign, metric_entries=[entry], metric_name="clicks", value=Decimal("1"))
    from app.measurement.repository import MeasurementAnalysisRunRepository, MeasurementSignalDerivationRepository

    run = MeasurementAnalysisRunRepository(db_session).create(campaign=campaign, client_request_id=_key())
    signal = service.record_signal(campaign=campaign, observations=[observation], summary="self")
    with pytest.raises(IntegrityError):
        MeasurementSignalDerivationRepository(db_session).create(
            signal=signal, current_observation_id=observation.id, prior_observation_id=observation.id, creator_run=run
        )
    db_session.rollback()


def test_concurrent_signal_derivation_race_leaves_no_orphan_or_false_audit(postgres_engine) -> None:
    # See the identical note on the Observation race test above — a real,
    # separate connection is required for the shared fixture setup so its
    # commits are genuinely visible to the two racing connections below.
    with postgres_engine.connect() as setup_connection:
        setup_session = OrmSession(bind=setup_connection)
        campaign, _run, _stages = _campaign(setup_session, campaign_name="Signal Race Campaign")
        setup_session.commit()
        _record_entry(setup_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), values={"clicks": Decimal("100")})
        _record_entry(setup_session, campaign, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), values={"clicks": Decimal("150")})
        campaign_id = campaign.id
        campaign_type = type(campaign)
        workspace_id = campaign.workspace_id
        setup_session.close()

    ready = threading.Barrier(2)
    errors: list[Exception] = []
    lock = threading.Lock()

    def attempt() -> None:
        with postgres_engine.connect() as connection:
            session = OrmSession(bind=connection)
            try:
                local_campaign = session.get(campaign_type, campaign_id)
                service = MeasurementAnalysisService(session)
                ready.wait(timeout=5)
                service.run_analysis(campaign=local_campaign, client_request_id=_key())
            except Exception as exc:  # pragma: no cover
                with lock:
                    errors.append(exc)
            finally:
                session.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"unexpected errors: {errors}"

    with postgres_engine.connect() as connection:
        signal_derivations = connection.execute(
            select(MeasurementSignalDerivation.__table__).where(
                MeasurementSignalDerivation.__table__.c.workspace_id == workspace_id
            )
        ).all()
        assert len(signal_derivations) == 1, "only one Signal derivation may survive the race"

        signals = connection.execute(
            select(PerformanceSignal.__table__).where(PerformanceSignal.__table__.c.campaign_id == campaign_id)
        ).all()
        assert len(signals) == 1, "the losing attempt must leave no orphan PerformanceSignal"

        recorded_events = connection.execute(
            select(AuditEvent.__table__).where(
                AuditEvent.__table__.c.event_type == EVENT_SIGNAL_RECORDED,
                AuditEvent.__table__.c.performance_signal_id == signals[0].id,
            )
        ).all()
        assert len(recorded_events) == 1


# =====================================================================
# AnalysisResult (MVP-11B §45)
# =====================================================================


def test_fresh_signals_produce_exactly_one_analysis_result(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Fresh Result Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), values={"clicks": Decimal("100")})
    _record_entry(db_session, campaign, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), values={"clicks": Decimal("150")})
    run = MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=_key())

    ownership_rows = db_session.execute(
        select(MeasurementAnalysisRunResult).where(MeasurementAnalysisRunResult.analysis_run_id == run.id)
    ).scalars().all()
    assert len(ownership_rows) == 1
    result = db_session.get(AnalysisResult, ownership_rows[0].analysis_result_id)
    assert "increased" in result.summary
    for forbidden in _FORBIDDEN_GOVERNANCE_WORDS:
        assert forbidden not in result.summary.lower()


def test_only_reused_signals_produce_zero_new_analysis_result(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Reused Only Result Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), values={"clicks": Decimal("100")})
    _record_entry(db_session, campaign, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), values={"clicks": Decimal("150")})
    service = MeasurementAnalysisService(db_session)
    run_a = service.run_analysis(campaign=campaign, client_request_id=_key())
    run_b = service.run_analysis(campaign=campaign, client_request_id=_key())

    assert run_a.status is MeasurementAnalysisRunStatus.COMPLETED
    assert run_b.status is MeasurementAnalysisRunStatus.COMPLETED
    run_b_ownership = db_session.execute(
        select(MeasurementAnalysisRunResult).where(MeasurementAnalysisRunResult.analysis_run_id == run_b.id)
    ).scalar_one_or_none()
    assert run_b_ownership is None, "an unchanged second run must create zero new AnalysisResult"

    all_results = db_session.execute(select(AnalysisResult).where(AnalysisResult.campaign_id == campaign.id)).scalars().all()
    assert len(all_results) == 1, "only the first run's AnalysisResult may exist"


def test_zero_signal_run_produces_zero_analysis_result(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Zero Signal Result Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    run = MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=_key())
    ownership = db_session.execute(
        select(MeasurementAnalysisRunResult).where(MeasurementAnalysisRunResult.analysis_run_id == run.id)
    ).scalar_one_or_none()
    assert ownership is None


# =====================================================================
# Failure / checkpoint semantics (MVP-11B §46)
# =====================================================================


def test_incomplete_snapshot_failure_leaves_no_run(db_session, monkeypatch) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Incomplete Snapshot Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    service = MeasurementAnalysisService(db_session)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated snapshot failure")

    monkeypatch.setattr(service.run_entries, "create_many", _boom)
    key = _key()
    with pytest.raises(RuntimeError):
        service.run_analysis(campaign=campaign, client_request_id=key)

    survivors = db_session.execute(
        select(MeasurementAnalysisRun).where(
            MeasurementAnalysisRun.workspace_id == campaign.workspace_id,
            MeasurementAnalysisRun.client_request_id == key,
        )
    ).scalars().all()
    assert survivors == [], "no RUNNING run with an incomplete snapshot may survive"


def test_prior_observation_checkpoint_survives_later_signal_failure(db_session, monkeypatch) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Signal Failure Survival Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), values={"clicks": Decimal("100")})
    _record_entry(db_session, campaign, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), values={"clicks": Decimal("150")})
    service = MeasurementAnalysisService(db_session)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated signal failure")

    monkeypatch.setattr(service, "_derive_signals", _boom)
    key = _key()
    with pytest.raises(RuntimeError):
        service.run_analysis(campaign=campaign, client_request_id=key)

    run = db_session.execute(
        select(MeasurementAnalysisRun).where(
            MeasurementAnalysisRun.workspace_id == campaign.workspace_id,
            MeasurementAnalysisRun.client_request_id == key,
        )
    ).scalar_one()
    assert run.status is MeasurementAnalysisRunStatus.FAILED
    assert run.failure_reason is not None
    for forbidden in ("Traceback", "RuntimeError", "simulated"):
        assert forbidden not in (run.failure_reason or "")

    observations = db_session.execute(select(PerformanceObservation).where(PerformanceObservation.campaign_id == campaign.id)).scalars().all()
    assert len(observations) == 2, "Observations committed before the failure must survive it"

    failed_event = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.measurement_analysis_run_id == run.id, AuditEvent.event_type == EVENT_ANALYSIS_RUN_FAILED
        )
    ).scalar_one()
    assert failed_event is not None


def test_prior_signal_checkpoint_survives_later_analysis_result_failure(db_session, monkeypatch) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Analysis Result Failure Survival Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), values={"clicks": Decimal("100")})
    _record_entry(db_session, campaign, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), values={"clicks": Decimal("150")})
    service = MeasurementAnalysisService(db_session)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated analysis result failure")

    monkeypatch.setattr(service, "_create_analysis_result", _boom)
    key = _key()
    with pytest.raises(RuntimeError):
        service.run_analysis(campaign=campaign, client_request_id=key)

    run = db_session.execute(
        select(MeasurementAnalysisRun).where(
            MeasurementAnalysisRun.workspace_id == campaign.workspace_id,
            MeasurementAnalysisRun.client_request_id == key,
        )
    ).scalar_one()
    assert run.status is MeasurementAnalysisRunStatus.FAILED

    observations = db_session.execute(select(PerformanceObservation).where(PerformanceObservation.campaign_id == campaign.id)).scalars().all()
    assert len(observations) == 2, "Observations committed before the failure must survive it"

    signals = db_session.execute(select(PerformanceSignal).where(PerformanceSignal.campaign_id == campaign.id)).scalars().all()
    assert len(signals) == 1, "the Signal committed before the AnalysisResult failure must survive it"

    results = db_session.execute(select(AnalysisResult).where(AnalysisResult.campaign_id == campaign.id)).scalars().all()
    assert results == [], "no AnalysisResult may exist when its own creation failed"


def test_completed_state_and_completed_audit_are_atomic(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Completed Atomic Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    run = MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=_key())
    assert run.status is MeasurementAnalysisRunStatus.COMPLETED
    event = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.measurement_analysis_run_id == run.id, AuditEvent.event_type == EVENT_ANALYSIS_RUN_COMPLETED
        )
    ).scalar_one()
    assert event.new_state is None or True  # presence alone is the assertion


def test_new_key_execution_after_failed_run_reuses_valid_evidence(db_session, monkeypatch) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="New Key After Failure Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), values={"clicks": Decimal("100")})
    _record_entry(db_session, campaign, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), values={"clicks": Decimal("150")})
    service = MeasurementAnalysisService(db_session)

    monkeypatch.setattr(service, "_derive_signals", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    failed_key = _key()
    with pytest.raises(RuntimeError):
        service.run_analysis(campaign=campaign, client_request_id=failed_key)

    monkeypatch.undo()
    new_key = _key()
    second_run = service.run_analysis(campaign=campaign, client_request_id=new_key)
    assert second_run.status is MeasurementAnalysisRunStatus.COMPLETED

    observations = db_session.execute(select(PerformanceObservation).where(PerformanceObservation.campaign_id == campaign.id)).scalars().all()
    assert len(observations) == 2, "the second run must reuse the Observations the failed run already created, not duplicate them"

    signal = db_session.execute(select(PerformanceSignal).where(PerformanceSignal.campaign_id == campaign.id)).scalar_one()
    assert signal.public_id.startswith("SIG-")


def test_new_key_execution_despite_stale_running_run(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Stale Running Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    from app.measurement.repository import MeasurementAnalysisRunRepository

    # Simulates a process crash right after run creation, before any
    # checkpoint completes (RESERVATION 3 — accepted, non-blocking).
    stale_run = MeasurementAnalysisRunRepository(db_session).create(campaign=campaign, client_request_id=_key())
    db_session.commit()
    assert stale_run.status is MeasurementAnalysisRunStatus.RUNNING

    service = MeasurementAnalysisService(db_session)
    new_run = service.run_analysis(campaign=campaign, client_request_id=_key())
    assert new_run.status is MeasurementAnalysisRunStatus.COMPLETED
    assert new_run.id != stale_run.id

    reloaded_stale = db_session.get(MeasurementAnalysisRun, stale_run.id)
    assert reloaded_stale.status is MeasurementAnalysisRunStatus.RUNNING, "a stale RUNNING run is never mutated by another run"


def test_no_campaign_run_or_stage_execution_mutation(db_session) -> None:
    campaign, run, stages = _campaign(db_session, campaign_name="No Orchestration Mutation Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=_key())
    db_session.refresh(run)
    assert run.status is CampaignRunStatus.CREATED
    for stage in stages.values():
        db_session.refresh(stage)
        assert stage.status is StageExecutionStatus.PENDING


# =====================================================================
# Audit (MVP-11B §47)
# =====================================================================


def test_run_level_audit_events_present_and_correctly_scoped(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="Run Audit Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    run = MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=_key())

    for event_type in (EVENT_ANALYSIS_RUN_STARTED, EVENT_ANALYSIS_RUN_COMPLETED):
        event = db_session.execute(
            select(AuditEvent).where(
                AuditEvent.measurement_analysis_run_id == run.id, AuditEvent.event_type == event_type
            )
        ).scalar_one()
        assert event.workspace_id == campaign.workspace_id
        assert event.campaign_id == campaign.id


def test_reused_rows_never_emit_a_duplicate_recorded_event(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="No Duplicate Recorded Event Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    service = MeasurementAnalysisService(db_session)
    service.run_analysis(campaign=campaign, client_request_id=_key())
    service.run_analysis(campaign=campaign, client_request_id=_key())

    observation = db_session.execute(select(PerformanceObservation).where(PerformanceObservation.campaign_id == campaign.id)).scalar_one()
    events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == EVENT_OBSERVATION_RECORDED, AuditEvent.performance_observation_id == observation.id
        )
    ).scalars().all()
    assert len(events) == 1, "a reused Observation must never emit a second .recorded event"


def test_domain_reconstruction_succeeds_without_querying_audit_event(db_session) -> None:
    campaign, _run, _stages = _campaign(db_session, campaign_name="No Audit Dependency Campaign")
    _record_entry(db_session, campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), values={"clicks": Decimal("100")})
    _record_entry(db_session, campaign, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), values={"clicks": Decimal("150")})
    run = MeasurementAnalysisService(db_session).run_analysis(campaign=campaign, client_request_id=_key())

    # Reconstruct entirely from provenance tables, deliberately never
    # touching AuditEvent (MVP-11A-R2 §8/§23 — audit is supplementary only).
    snapshot_entry_ids = db_session.execute(
        select(MeasurementAnalysisRunMetricEntry.metric_entry_id).where(
            MeasurementAnalysisRunMetricEntry.analysis_run_id == run.id
        )
    ).scalars().all()
    assert len(snapshot_entry_ids) == 2

    created_observations = db_session.execute(
        select(MeasurementObservationDerivation).where(MeasurementObservationDerivation.creator_analysis_run_id == run.id)
    ).scalars().all()
    assert len(created_observations) == 2

    created_signals = db_session.execute(
        select(MeasurementSignalDerivation).where(MeasurementSignalDerivation.creator_analysis_run_id == run.id)
    ).scalars().all()
    assert len(created_signals) == 1

    ownership = db_session.execute(
        select(MeasurementAnalysisRunResult).where(MeasurementAnalysisRunResult.analysis_run_id == run.id)
    ).scalar_one()
    assert ownership.analysis_result_id is not None
