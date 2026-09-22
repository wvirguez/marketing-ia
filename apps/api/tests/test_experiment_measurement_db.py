"""DB-level constraint / adversarial-insert tests for Experiment Measurement
(frozen Final Relational Integrity Reconciliation). Real PostgreSQL only;
raw ORM inserts, never through the service (so no REPEATABLE READ isolation
concerns — these tests exercise CHECK/FK/UNIQUE constraints directly)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.strategy.models import (
    ExperimentMeasurementDatumUsage,
    ExperimentMeasurementRun,
    ExperimentMeasurementSignalOutput,
    ExperimentMeasurementSliceOutput,
)
from tests.declarationtest import build_experiment_with_definition
from tests.evidenceclaimtest import make_entry
from tests.experimentmeasurementtest import add_claim, build_started
from tests.test_execution_authorization_domain import _authorize, _declare_variant

pytestmark = pytest.mark.postgres


def _minimal_run(db_session, started, *, contract_version_id=None, experiment_id=None) -> ExperimentMeasurementRun:
    run = ExperimentMeasurementRun(
        public_id="EXM-" + uuid.uuid4().hex[:12].upper(),
        workspace_id=started.campaign.workspace_id,
        experiment_id=experiment_id or started.experiment.id,
        authorization_id=started.authorization.id,
        start_id=started.start.id,
        contract_version_id=contract_version_id or started.authorization.contract_version_id,
        declaration_semantics_version=1,
        client_request_id=uuid.uuid4().hex,
        created_by_user_id=started.actor.id,
    )
    db_session.add(run)
    db_session.flush()
    return run


# --- RI-1: Run <-> SignalOutput contract/experiment lineage -----------------


def test_signal_output_with_wrong_contract_is_rejected(db_session) -> None:
    started = build_started(db_session, level="DESCRIPTIVE")
    run = _minimal_run(db_session, started)

    # A second, unrelated Contract (different experiment/definition/campaign).
    other_campaign, other_experiment, other_actor, other_version = build_experiment_with_definition(
        db_session, campaign_name="Other Campaign RI1"
    )
    _declare_variant(db_session, other_campaign, other_experiment, other_actor, other_version, label="A", key="v-x")
    from tests.declarationtest import declare, descriptive_signals

    other_contract = declare(
        db_session, other_campaign, other_experiment, other_actor, other_version,
        signals=descriptive_signals(), level="DESCRIPTIVE", window=14, key="c-x",
    )[0]

    bad = ExperimentMeasurementSignalOutput(
        workspace_id=run.workspace_id,
        run_id=run.id,
        required_signal_id=started.signals[0].id,
        contract_version_id=other_contract.id,  # WRONG contract
        experiment_id=run.experiment_id,
        declaration_level="DESCRIPTIVE",
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_signal_output_with_wrong_experiment_is_rejected(db_session) -> None:
    started = build_started(db_session, level="DESCRIPTIVE")
    run = _minimal_run(db_session, started)
    other_campaign, other_experiment, _other_actor, _other_version = build_experiment_with_definition(
        db_session, campaign_name="Other Campaign RI1b"
    )
    bad = ExperimentMeasurementSignalOutput(
        workspace_id=run.workspace_id,
        run_id=run.id,
        required_signal_id=started.signals[0].id,
        contract_version_id=run.contract_version_id,
        experiment_id=other_experiment.id,  # WRONG experiment
        declaration_level="DESCRIPTIVE",
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# --- RI-2: DatumUsage <-> Run/Claim start lineage ---------------------------


def test_datum_usage_from_a_different_starts_claim_is_rejected(db_session) -> None:
    started_a = build_started(db_session, level="DESCRIPTIVE")
    started_b = build_started(db_session, level="DESCRIPTIVE")
    run_a = _minimal_run(db_session, started_a)
    claim_b, _created, _entry = add_claim(
        db_session, started_b, period_start=started_b.start.started_at.date(), period_end=started_b.start.started_at.date()
    )
    bad = ExperimentMeasurementDatumUsage(
        workspace_id=run_a.workspace_id,
        run_id=run_a.id,
        start_id=run_a.start_id,  # Run A's start
        claim_id=claim_b.id,  # but a claim from Start B
        required_signal_id=claim_b.required_signal_id,
        channel="email",
        temporal_role=None,
        usage_decision="CONSUMED",
        recorded_before_declaration=False,
        later_grouping_entry_exists_at_run=False,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# --- §7: DatumUsage <-> Claim's true signal ---------------------------------


def test_datum_usage_labelling_a_claim_under_the_wrong_signal_is_rejected(db_session) -> None:
    started = build_started(
        db_session, level=None, signals=[__import__("tests.declarationtest", fromlist=["legacy_signal"]).legacy_signal(name="Signal A"),
                                          __import__("tests.declarationtest", fromlist=["legacy_signal"]).legacy_signal(name="Signal B")],
    )
    run = _minimal_run(db_session, started)
    claim_row, _created, _entry = add_claim(
        db_session, started, period_start=started.start.started_at.date(), period_end=started.start.started_at.date(),
        signal=started.signals[0],
    )
    bad = ExperimentMeasurementDatumUsage(
        workspace_id=run.workspace_id,
        run_id=run.id,
        start_id=run.start_id,
        claim_id=claim_row.id,
        required_signal_id=started.signals[1].id,  # WRONG signal (claim really belongs to signals[0])
        channel="email",
        temporal_role=None,
        usage_decision="CONSUMED",
        recorded_before_declaration=False,
        later_grouping_entry_exists_at_run=False,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# --- RI-3: slice pair-value invariant ---------------------------------------


def _signal_output(db_session, started, *, run, declaration_level="COMPARATIVE"):
    so = ExperimentMeasurementSignalOutput(
        workspace_id=run.workspace_id,
        run_id=run.id,
        required_signal_id=started.signals[0].id,
        contract_version_id=run.contract_version_id,
        experiment_id=run.experiment_id,
        declaration_level=declaration_level,
    )
    db_session.add(so)
    db_session.flush()
    return so


def test_pairing_state_not_pair_with_values_populated_is_rejected(db_session) -> None:
    started = build_started(db_session, level="COMPARATIVE", baseline=7)
    run = _minimal_run(db_session, started)
    so = _signal_output(db_session, started, run=run)
    bad = ExperimentMeasurementSliceOutput(
        workspace_id=run.workspace_id,
        signal_output_id=so.id,
        channel="email",
        qualifying_count=1,
        required_count=None,
        coverage_state=None,
        pairing_state="INCOMPLETE",
        ambiguous_excluded_count=0,
        conflict_excluded_count=0,
        multi_signal_excluded_count=0,
        out_of_window_count=0,
        baseline_value=Decimal("1"),
        observation_value=Decimal("2"),
        signed_arithmetic_difference=Decimal("1"),
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_pairing_state_pair_without_values_is_rejected(db_session) -> None:
    started = build_started(db_session, level="COMPARATIVE", baseline=7)
    run = _minimal_run(db_session, started)
    so = _signal_output(db_session, started, run=run)
    bad = ExperimentMeasurementSliceOutput(
        workspace_id=run.workspace_id,
        signal_output_id=so.id,
        channel="email",
        qualifying_count=2,
        required_count=None,
        coverage_state=None,
        pairing_state="PAIR",
        ambiguous_excluded_count=0,
        conflict_excluded_count=0,
        multi_signal_excluded_count=0,
        out_of_window_count=0,
        baseline_value=None,
        observation_value=None,
        signed_arithmetic_difference=None,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_coverage_without_required_count_is_rejected(db_session) -> None:
    started = build_started(db_session, level="DESCRIPTIVE")
    run = _minimal_run(db_session, started)
    so = _signal_output(db_session, started, run=run, declaration_level="DESCRIPTIVE")
    bad = ExperimentMeasurementSliceOutput(
        workspace_id=run.workspace_id,
        signal_output_id=so.id,
        channel="email",
        qualifying_count=0,
        required_count=None,  # missing
        coverage_state="NOT_COVERED",
        pairing_state=None,
        ambiguous_excluded_count=0,
        conflict_excluded_count=0,
        multi_signal_excluded_count=0,
        out_of_window_count=0,
        baseline_value=None,
        observation_value=None,
        signed_arithmetic_difference=None,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_pairing_with_required_count_is_rejected(db_session) -> None:
    started = build_started(db_session, level="COMPARATIVE", baseline=7)
    run = _minimal_run(db_session, started)
    so = _signal_output(db_session, started, run=run)
    bad = ExperimentMeasurementSliceOutput(
        workspace_id=run.workspace_id,
        signal_output_id=so.id,
        channel="email",
        qualifying_count=0,
        required_count=1,  # not allowed for COMPARATIVE
        coverage_state=None,
        pairing_state="INCOMPLETE",
        ambiguous_excluded_count=0,
        conflict_excluded_count=0,
        multi_signal_excluded_count=0,
        out_of_window_count=0,
        baseline_value=None,
        observation_value=None,
        signed_arithmetic_difference=None,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_negative_counts_are_rejected(db_session) -> None:
    started = build_started(db_session, level="DESCRIPTIVE")
    run = _minimal_run(db_session, started)
    so = _signal_output(db_session, started, run=run, declaration_level="DESCRIPTIVE")
    bad = ExperimentMeasurementSliceOutput(
        workspace_id=run.workspace_id,
        signal_output_id=so.id,
        channel="email",
        qualifying_count=-1,
        required_count=1,
        coverage_state="NOT_COVERED",
        pairing_state=None,
        ambiguous_excluded_count=0,
        conflict_excluded_count=0,
        multi_signal_excluded_count=0,
        out_of_window_count=0,
        baseline_value=None,
        observation_value=None,
        signed_arithmetic_difference=None,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_invalid_usage_decision_vocabulary_is_rejected(db_session) -> None:
    started = build_started(db_session, level=None)
    run = _minimal_run(db_session, started)
    claim_row, _created, _entry = add_claim(
        db_session, started, period_start=started.start.started_at.date(), period_end=started.start.started_at.date()
    )
    bad = ExperimentMeasurementDatumUsage(
        workspace_id=run.workspace_id,
        run_id=run.id,
        start_id=run.start_id,
        claim_id=claim_row.id,
        required_signal_id=claim_row.required_signal_id,
        channel="email",
        temporal_role=None,
        usage_decision="RESULT_WINNER",  # not in the frozen vocabulary
        recorded_before_declaration=False,
        later_grouping_entry_exists_at_run=False,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_temporal_role_with_excluded_out_of_window_is_rejected(db_session) -> None:
    started = build_started(db_session, level="DESCRIPTIVE")
    run = _minimal_run(db_session, started)
    claim_row, _created, _entry = add_claim(
        db_session, started, period_start=started.start.started_at.date(), period_end=started.start.started_at.date()
    )
    bad = ExperimentMeasurementDatumUsage(
        workspace_id=run.workspace_id,
        run_id=run.id,
        start_id=run.start_id,
        claim_id=claim_row.id,
        required_signal_id=claim_row.required_signal_id,
        channel="email",
        temporal_role="BASELINE",  # illegal alongside EXCLUDED_OUT_OF_WINDOW
        usage_decision="EXCLUDED_OUT_OF_WINDOW",
        recorded_before_declaration=False,
        later_grouping_entry_exists_at_run=False,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_legacy_datum_usage_with_no_signal_output_still_persists(db_session) -> None:
    """Proves the RI-2/§7 fix: a Legacy run (zero SignalOutput rows) can
    still persist DatumUsage rows — resolving a real implementation-time
    finding where an unconditional FK made this impossible."""
    started = build_started(db_session, level=None)
    run = _minimal_run(db_session, started)
    claim_row, _created, _entry = add_claim(
        db_session, started, period_start=started.start.started_at.date(), period_end=started.start.started_at.date()
    )
    row = ExperimentMeasurementDatumUsage(
        workspace_id=run.workspace_id,
        run_id=run.id,
        start_id=run.start_id,
        claim_id=claim_row.id,
        required_signal_id=claim_row.required_signal_id,
        channel="email",
        temporal_role=None,
        usage_decision="CONSUMED",
        recorded_before_declaration=False,
        later_grouping_entry_exists_at_run=False,
    )
    db_session.add(row)
    db_session.flush()  # must NOT raise
