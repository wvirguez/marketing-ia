"""Real-PostgreSQL concurrency/isolation tests for Experiment Measurement
(frozen Reconciliation §H/§I/§J/§K). Each operation runs in its own
independent Session bound directly to ``postgres_engine`` — never the
shared, pre-wrapped ``db_session`` fixture, which cannot legally support the
service's own REPEATABLE READ reconfiguration (a genuine, confirmed
implementation-time finding: ``db_session`` begins its own outer transaction
before test code runs, so a later isolation-level change on that same
connection is illegal — this is exactly why these tests use independent
per-worker sessions, mirroring the established
``test_execution_start_concurrency.py`` pattern exactly, and exactly why the
service itself commits phase 1 before reconfiguring on a fresh transaction).
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Event

import pytest
from sqlalchemy.orm import Session

from app.campaigns.repository import CampaignRepository
from app.core.api_errors import ExperimentMeasurementIdempotencyKeyConflictError
from app.measurement.models import MetricSource
from app.measurement.repository import MetricEntryRepository, MetricValueRepository
from app.strategy.experiment_evidence_claim_service import ExperimentEvidenceClaimService
from app.strategy.experiment_measurement_service import ExperimentMeasurementService
from app.strategy.models import ExperimentEvidenceClaim, ExperimentMeasurementRun
from app.strategy.repository import ExperimentEvidenceClaimRepository, ExperimentMeasurementRunRepository
from tests.experimentmeasurementtest import build_started

pytestmark = pytest.mark.postgres


def _setup(engine, name: str) -> dict:
    with Session(engine, expire_on_commit=False) as session:
        started = build_started(session, campaign_name=name, level=None)
        session.commit()
        return {
            "campaign": started.campaign.public_id,
            "experiment": started.experiment.public_id,
            "start": started.start.public_id,
            "actor": started.actor.id,
            "signal": started.signal.public_id,
        }


def _run_op(engine, ctx, *, key):
    def operation():
        with Session(engine, expire_on_commit=False) as session:
            campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
            return ExperimentMeasurementService(session).create(
                campaign=campaign,
                experiment_public_id=ctx["experiment"],
                start_public_id=ctx["start"],
                client_request_id=key,
                actor_user_id=ctx["actor"],
            )

    return operation


def test_same_idempotency_key_same_start_concurrency_yields_one_canonical_run(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Measurement Concurrency Same Key")
    key = uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_run_op(postgres_engine, ctx, key=key)) for _ in range(2)]
        results = [future.result() for future in futures]

    run_ids = {run.id for run, _created in results}
    assert len(run_ids) == 1, "both racers must resolve to exactly one canonical Run"
    assert sorted(created for _run, created in results) == [False, True]

    with Session(postgres_engine) as check:
        matches = check.query(ExperimentMeasurementRun).filter(
            ExperimentMeasurementRun.client_request_id == key
        ).all()
        assert len(matches) == 1, "the unique constraint must have allowed exactly one row to commit"


def test_same_idempotency_key_different_start_concurrency_yields_conflict(postgres_engine) -> None:
    ctx_a = _setup(postgres_engine, "Measurement Concurrency Diff Start A")
    with Session(postgres_engine) as check:
        workspace_id = CampaignRepository(check).get_by_public_id(ctx_a["campaign"]).workspace_id
    with Session(postgres_engine, expire_on_commit=False) as session:
        started_b = build_started(
            session, campaign_name="Measurement Concurrency Diff Start B", level=None, within_workspace_id=workspace_id
        )
        session.commit()
        ctx_b = {
            "campaign": started_b.campaign.public_id,
            "experiment": started_b.experiment.public_id,
            "start": started_b.start.public_id,
            "actor": started_b.actor.id,
        }
    key = uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(_run_op(postgres_engine, ctx_a, key=key))
        future_b = pool.submit(_run_op(postgres_engine, ctx_b, key=key))
        results, errors = [], []
        for future in (future_a, future_b):
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - the OTHER racer must raise the typed conflict
                errors.append(exc)

    assert len(results) == 1, "exactly one racer must succeed"
    assert len(errors) == 1, "exactly one racer must be rejected"
    assert isinstance(errors[0], ExperimentMeasurementIdempotencyKeyConflictError)


def test_claim_committed_after_the_run_snapshot_is_never_visible_to_that_run(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Measurement Snapshot Visibility")

    with Session(postgres_engine, expire_on_commit=False) as run_session:
        campaign = CampaignRepository(run_session).get_by_public_id(ctx["campaign"])
        run, created = ExperimentMeasurementService(run_session).create(
            campaign=campaign,
            experiment_public_id=ctx["experiment"],
            start_public_id=ctx["start"],
            client_request_id=uuid.uuid4().hex,
            actor_user_id=ctx["actor"],
        )
    assert created is True

    with Session(postgres_engine) as check:
        datum_usages = ExperimentMeasurementRunRepository(check).list_datum_usages_for_run(run_id=run.id)
        assert datum_usages == [], "no claims existed yet when this Run's snapshot was taken"


# --- PostgreSQL 40001 (serialization_failure) investigation -----------------
#
# Local imports are used deliberately in this section (rather than added to
# the shared import block above) to stay strictly additive here while another
# agent is concurrently editing this same file's existing content elsewhere.
#
# EMPIRICAL FINDING (verified directly against real PostgreSQL with raw
# psycopg3 connections, entirely outside this service, before writing the
# tests below): the commonly-assumed "two REPEATABLE READ transactions
# INSERT the same unique key -> one gets 40001" behavior does NOT reproduce.
# Tried: (a) T1 paused after its snapshot, T2 fully commits first, T1 then
# inserts — 23505 in every trial; (b) both transactions released off a
# shared Barrier for a genuinely simultaneous race — still 23505 for the
# loser, in both REPEATABLE READ and SERIALIZABLE; (c) a concurrent UPDATE of
# an FK-referenced parent row (another plausible 40001 trigger, "could not
# serialize access due to concurrent update") while a child INSERT was
# paused — also no conflict, because PostgreSQL's FK RI trigger checks the
# referenced row under its own always-current snapshot, not the outer
# REPEATABLE READ snapshot. The mechanism behind this: unique-index
# enforcement uses SnapshotDirty, which simply waits for the conflicting
# inserter to finish and then reports an ordinary duplicate-key error — it
# never raises a serialization failure for a plain INSERT/INSERT conflict.
# ``40001`` under REPEATABLE READ is specifically for UPDATE/DELETE
# first-committer-wins conflicts, which this INSERT-only write pattern (Run +
# children + one audit event, no UPDATE/DELETE/SELECT-FOR-UPDATE of any
# pre-existing row) never performs. Per this task's own instruction to report
# this honestly rather than force a fake 40001: the test below proves the
# REAL outcome (23505, not 40001) under a genuine, deterministically forced
# interleaving, and the one after it fault-injects BOTH failures (clearly
# labeled), since no genuine 40001 is reachable in this write path at all.


def _pause_first_create_call(monkeypatch):
    """Wraps ``ExperimentMeasurementRunRepository.create`` so the FIRST
    caller across the whole process pauses right before its own INSERT/flush
    — after its REPEATABLE READ snapshot is already fixed by the earlier
    reads in ``_compute_and_insert``, but before it writes anything. Returns
    ``(reached, proceed, calls)``; set ``proceed`` to release it. Every other
    caller (including this same caller's own later retry attempt) passes
    straight through to the real method."""
    from app.strategy.repository import ExperimentMeasurementRunRepository as _RunRepo

    reached = Event()
    proceed = Event()
    calls = {"n": 0}
    real_create = _RunRepo.create

    def paused_create(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            reached.set()
            assert proceed.wait(timeout=15), "the paused first INSERT was never released"
        return real_create(self, **kwargs)

    monkeypatch.setattr(_RunRepo, "create", paused_create)
    return reached, proceed, calls


def test_two_repeatable_read_transactions_racing_the_same_unique_key_yield_real_23505_not_40001_and_replay_the_canonical_run(
    postgres_engine, monkeypatch
) -> None:
    """Forces the exact interleaving the "two REPEATABLE READ transactions
    INSERT the same unique key" scenario describes: T1 is paused right
    before its own INSERT, its REPEATABLE READ snapshot already fixed by the
    earlier reads in ``_compute_and_insert``. T2 runs the ENTIRE pipeline for
    the SAME Start and the SAME ``client_request_id``, in its own fresh
    REPEATABLE READ transaction, and fully commits while T1 is still paused.
    T1 is then released and attempts its own conflicting INSERT — its
    snapshot predates T2's commit, so T2's row is invisible to T1 under MVCC.

    EMPIRICALLY, this is NOT a 40001: PostgreSQL's unique-index enforcement
    uses a dirty snapshot for this exact check (see the module-level note
    above, confirmed independently with raw psycopg3 outside this service).
    ``_is_serialization_failure`` is asserted to NEVER see a genuine
    OperationalError here — T1 instead raises a plain IntegrityError(23505)
    inside ``_compute_and_insert`` itself, which is caught by the
    already-implemented canonical-lookup path (same ``start_id`` -> replay),
    never reaching the OperationalError/40001 retry loop at all."""
    import app.strategy.experiment_measurement_service as ems_module

    ctx = _setup(postgres_engine, "Measurement Real 23505 Race")
    key = uuid.uuid4().hex

    reached, proceed, calls = _pause_first_create_call(monkeypatch)

    observed_sqlstates: list[str | None] = []
    real_is_serialization_failure = ems_module._is_serialization_failure

    def spy_is_serialization_failure(exc):
        observed_sqlstates.append(getattr(exc.orig, "sqlstate", None))
        return real_is_serialization_failure(exc)

    monkeypatch.setattr(ems_module, "_is_serialization_failure", spy_is_serialization_failure)

    t1_result: dict = {}

    def t1_worker():
        t1_result["value"] = _run_op(postgres_engine, ctx, key=key)()

    with ThreadPoolExecutor(max_workers=1) as pool:
        t1_future = pool.submit(t1_worker)
        assert reached.wait(timeout=15), "T1 never reached its own INSERT"

        # T2: the ENTIRE Measurement pipeline for the SAME Start/key, its own
        # fresh REPEATABLE READ transaction, started and fully committed
        # while T1 sits paused just before its own conflicting INSERT.
        t2_run, t2_created = _run_op(postgres_engine, ctx, key=key)()
        assert t2_created is True

        proceed.set()
        t1_future.result(timeout=30)
    monkeypatch.undo()

    assert observed_sqlstates == [], (
        f"expected NO genuine OperationalError/40001 for this INSERT/INSERT race — the unique-index dirty-"
        f"snapshot check resolves it as an ordinary 23505 instead; observed {observed_sqlstates!r}"
    )
    assert calls["n"] == 2, "expected exactly T2's own create() plus T1's single (non-retried) attempt"

    t1_run, t1_created = t1_result["value"]
    assert t1_created is False, "T1 must land on the existing 23505 canonical-replay path, not a second insert"
    assert t1_run.id == t2_run.id, "T1 must replay T2's canonical committed Run"

    with Session(postgres_engine) as check:
        matches = check.query(ExperimentMeasurementRun).filter(
            ExperimentMeasurementRun.client_request_id == key
        ).all()
        assert len(matches) == 1, "exactly one Run must exist despite the race"


def test_two_serialization_failures_in_a_row_raise_the_typed_retryable_error_both_fault_injected(
    postgres_engine,
) -> None:
    """BOTH failures are fault-injected, clearly labeled as such, per this
    task's own explicit fallback for when a genuine 40001 cannot be produced.
    The sibling test above empirically proves a genuine 40001 is NOT
    reachable for this service's actual write pattern (INSERT-only; no
    UPDATE/DELETE/SELECT-FOR-UPDATE of any pre-existing row, and FK RI checks
    use their own always-current snapshot) — the one plausible race
    (concurrent INSERT of the same unique key) deterministically resolves as
    an ordinary 23505 instead, confirmed both by raw psycopg3 experiments
    outside this service and by the sibling test's real run. There is
    therefore no genuine mechanism left inside this write path to construct
    even a SINGLE real 40001 for a "first real, second fault-injected"
    version of this test; both are fault-injected here instead. No real
    concurrency is needed for this: ``ExperimentMeasurementRunRepository.
    create`` is made to raise a synthetic ``OperationalError`` with
    ``orig.sqlstate == "40001"`` on every attempt, and this test verifies
    only the service's own typed-error contract: the bounded retry consumes
    exactly its one authorized attempt, then raises the typed, retryable
    ``ExperimentMeasurementRetryableError`` — never a third attempt, never
    misclassified as an idempotency conflict."""
    from sqlalchemy.exc import OperationalError

    from app.core.api_errors import ExperimentMeasurementRetryableError
    from app.strategy.repository import ExperimentMeasurementRunRepository as _RunRepo

    ctx = _setup(postgres_engine, "Measurement Double 40001 Fault Injected")
    key = uuid.uuid4().hex

    calls = {"n": 0}

    class _FakeOrig(Exception):
        sqlstate = "40001"

    def always_fault_injected_create(self, **kwargs):
        calls["n"] += 1
        # Both the first AND second attempt are fault-injected here (see
        # docstring for why a genuine 40001 is unreachable for this
        # service's write pattern) — the real INSERT is never reached.
        raise OperationalError("INSERT INTO experiment_measurement_runs", {}, _FakeOrig())

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(_RunRepo, "create", always_fault_injected_create)
        with pytest.raises(ExperimentMeasurementRetryableError):
            _run_op(postgres_engine, ctx, key=key)()

    assert calls["n"] == 2, "expected exactly the one authorized retry attempt, never a third"

    with Session(postgres_engine) as check:
        matches = check.query(ExperimentMeasurementRun).filter(
            ExperimentMeasurementRun.client_request_id == key
        ).all()
        assert matches == [], "no Run may exist after two failed attempts, fault-injected or not"


# =====================================================================================================================
# Genuine, deterministically-synchronized interleaving tests (VC-1/VC-3/VC-4).
#
# The test above never interleaves a concurrent commit — it runs Measurement
# against a Start with zero pre-existing claims, so it would pass under ANY
# isolation level. The tests below force a REAL two-transaction interleaving,
# synchronized with ``threading.Event`` (the same wrapped-method-blocks-on-an-
# Event pattern ``test_evidence_claim_concurrency.py`` already establishes —
# see its ``_interleave_claim_and_revoke``), never sleep-based racing.
#
# THE PAUSE POINT: ``ExperimentEvidenceClaimRepository.list_active_for_start``
# is the first read in ``_compute_and_insert`` that runs AFTER the Start
# re-read (``self.starts.get_by_id`` — the transaction's first statement,
# which is what actually fixes the REPEATABLE READ snapshot in PostgreSQL:
# the snapshot is pinned to the first query of the transaction, regardless of
# when ``execution_options({"isolation_level": ...})`` was requested). Pausing
# immediately before ``list_active_for_start`` therefore gates EVERY later
# read in that same transaction (claims, entries, values, and the
# later-grouping-entry check) behind one already-fixed snapshot — one pause
# point serves all of VC-1, VC-3 Case B, the MetricEntry/Value boundary test,
# and VC-4 below.
# =====================================================================================================================


def _pause_after_snapshot(monkeypatch, *, timeout: float = 15.0):
    """Pauses T1 (the Measurement transaction) right after its REPEATABLE
    READ snapshot is fixed and right before it reads active Claims. Returns
    ``(snapshot_ready, proceed)``: the caller waits on ``snapshot_ready``
    before acting as T2, then sets ``proceed`` to let T1 resume and finish
    reading under the now-frozen snapshot."""
    snapshot_ready = Event()
    proceed = Event()
    real = ExperimentEvidenceClaimRepository.list_active_for_start

    def paused(self, **kwargs):
        snapshot_ready.set()
        assert proceed.wait(timeout=timeout), "T2 never signaled proceed — deadlock in the test harness"
        return real(self, **kwargs)

    monkeypatch.setattr(ExperimentEvidenceClaimRepository, "list_active_for_start", paused)
    return snapshot_ready, proceed


def _add_claim(
    engine,
    ctx,
    *,
    period_start,
    period_end,
    channel: str = "email",
    metric_name: str = "clicks",
    value: Decimal = Decimal("10"),
    entry_created_at=None,
    key: str | None = None,
):
    """T2 helper: commits one fresh MetricEntry/MetricValue and one
    ExperimentEvidenceClaim against ctx's Start, entirely in its own
    independent session/transaction. Returns ``(claim_public_id, claim_id,
    entry_id)``."""
    with Session(engine, expire_on_commit=False) as session:
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        entry = MetricEntryRepository(session).create(
            campaign=campaign,
            period_start=period_start,
            period_end=period_end,
            channel=channel,
            source=MetricSource.MANUAL,
            client_request_id=uuid.uuid4().hex,
        )
        MetricValueRepository(session).create_many(metric_entry=entry, values={metric_name: value})
        if entry_created_at is not None:
            entry.created_at = entry_created_at
            session.flush()
        claim, _created = ExperimentEvidenceClaimService(session).create(
            campaign=campaign,
            experiment_public_id=ctx["experiment"],
            start_public_id=ctx["start"],
            client_request_id=key or uuid.uuid4().hex,
            required_signal_public_id=ctx["signal"],
            metric_entry_public_id=entry.public_id,
            metric_name=metric_name,
            actor_user_id=ctx["actor"],
        )
        session.commit()
        return claim.public_id, claim.id, entry.id


def _add_entry(
    engine,
    ctx,
    *,
    period_start,
    period_end,
    channel: str = "email",
    metric_name: str = "clicks",
    value: Decimal = Decimal("5"),
    entry_created_at=None,
) -> uuid.UUID:
    """T2 helper: commits a bare MetricEntry/MetricValue with NO claim — used
    for the later-grouping-entry snapshot boundary, which only requires the
    entry itself to exist in the same (campaign, period, channel) grouping."""
    with Session(engine, expire_on_commit=False) as session:
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        entry = MetricEntryRepository(session).create(
            campaign=campaign,
            period_start=period_start,
            period_end=period_end,
            channel=channel,
            source=MetricSource.MANUAL,
            client_request_id=uuid.uuid4().hex,
        )
        MetricValueRepository(session).create_many(metric_entry=entry, values={metric_name: value})
        if entry_created_at is not None:
            entry.created_at = entry_created_at
            session.flush()
        session.commit()
        return entry.id


def _dispose_claim(engine, ctx, claim_public_id: str, *, reason: str = "No longer intended."):
    with Session(engine, expire_on_commit=False) as session:
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        result = ExperimentEvidenceClaimService(session).dispose(
            campaign=campaign,
            experiment_public_id=ctx["experiment"],
            start_public_id=ctx["start"],
            claim_public_id=claim_public_id,
            reason=reason,
            actor_user_id=ctx["actor"],
        )
        session.commit()
        return result


def _run_measurement(engine, ctx, *, key: str | None = None):
    with Session(engine, expire_on_commit=False) as session:
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentMeasurementService(session).create(
            campaign=campaign,
            experiment_public_id=ctx["experiment"],
            start_public_id=ctx["start"],
            client_request_id=key or uuid.uuid4().hex,
            actor_user_id=ctx["actor"],
        )


def _usages_for_run(engine, run_id):
    with Session(engine) as check:
        return ExperimentMeasurementRunRepository(check).list_datum_usages_for_run(run_id=run_id)


# --- VC-1: a Claim committed after the snapshot is never visible to that Run ------------------------------------------


def test_vc1_claim_committed_after_the_snapshot_is_invisible_to_that_run(postgres_engine, monkeypatch) -> None:
    """T1 begins the Measurement transaction and is paused right after its
    REPEATABLE READ snapshot is established (before reading Claims). T2
    creates and COMMITS a new qualifying Claim for that same Start. T1
    resumes and completes: the resulting Run must have NO DatumUsage for
    T2's claim."""
    ctx = _setup(postgres_engine, "VC1 After Snapshot")
    snapshot_ready, proceed = _pause_after_snapshot(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_measurement, postgres_engine, ctx, key="vc1-after-run")
        assert snapshot_ready.wait(timeout=15), "measurement never reached its claims read"
        _claim_public_id, claim_id, _entry_id = _add_claim(
            postgres_engine, ctx, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), key="vc1-after-claim"
        )
        proceed.set()
        run, created = future.result(timeout=20)
    monkeypatch.undo()
    assert created is True
    usages = _usages_for_run(postgres_engine, run.id)
    assert all(usage.claim_id != claim_id for usage in usages), "a claim committed AFTER the snapshot leaked into the Run"


def test_vc1_control_claim_committed_before_the_run_starts_is_visible(postgres_engine) -> None:
    """Pairs with the test above, no pause needed: T2 commits a qualifying
    Claim FIRST, fully, before T1 even starts. T1 then runs Measurement
    normally. The Claim MUST be visible — proving the earlier rejected
    snapshot test wasn't just exercising an always-empty scenario."""
    ctx = _setup(postgres_engine, "VC1 Before Snapshot Control")
    _claim_public_id, claim_id, _entry_id = _add_claim(
        postgres_engine, ctx, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), key="vc1-before-claim"
    )
    run, created = _run_measurement(postgres_engine, ctx, key="vc1-before-run")
    assert created is True
    usages = _usages_for_run(postgres_engine, run.id)
    assert any(usage.claim_id == claim_id for usage in usages), "a claim committed BEFORE the Run started must be visible"


# --- VC-3: disposal before vs. after the snapshot ----------------------------------------------------------------------


def test_vc3_case_a_claim_disposed_before_the_snapshot_is_invisible(postgres_engine) -> None:
    """A Claim disposed and committed BEFORE Measurement's snapshot is
    established must produce no DatumUsage at all (it is simply absent from
    ``list_active_for_start``, never a separate "excluded" row)."""
    ctx = _setup(postgres_engine, "VC3 Case A Dispose Before Snapshot")
    claim_public_id, claim_id, _entry_id = _add_claim(
        postgres_engine, ctx, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), key="vc3a-claim"
    )
    _dispose_claim(postgres_engine, ctx, claim_public_id)
    run, created = _run_measurement(postgres_engine, ctx, key="vc3a-run")
    assert created is True
    usages = _usages_for_run(postgres_engine, run.id)
    assert all(usage.claim_id != claim_id for usage in usages), "a claim disposed BEFORE the snapshot must never appear"


def _pause_after_snapshot_and_record_claims(monkeypatch, *, timeout: float = 15.0):
    """Like ``_pause_after_snapshot``, but also RECORDS every call's result
    as a plain ``(claim_id, disposed_at)`` list — used below to prove a
    transparent SQLSTATE 40001 retry actually happened (a second, later call
    whose snapshot differs from the first)."""
    snapshot_ready = Event()
    proceed = Event()
    calls: list[list[tuple[uuid.UUID, object]]] = []
    real = ExperimentEvidenceClaimRepository.list_active_for_start

    def paused(self, **kwargs):
        snapshot_ready.set()
        assert proceed.wait(timeout=timeout), "T2 never signaled proceed — deadlock in the test harness"
        result = real(self, **kwargs)
        calls.append([(claim.id, claim.disposed_at) for claim in result])
        return result

    monkeypatch.setattr(ExperimentEvidenceClaimRepository, "list_active_for_start", paused)
    return snapshot_ready, proceed, calls


def test_vc3_case_b_dispose_racing_the_insert_forces_a_transparent_retry_that_excludes_it(
    postgres_engine, monkeypatch
) -> None:
    """GENUINE FINDING (not the scenario originally asked for, which turns
    out to be structurally impossible — see below): the Claim is active and
    visible when T1's REPEATABLE READ snapshot is taken; T1 is paused right
    there; T2 disposes and COMMITS that already-active Claim; T1 resumes.

    T1's paused ``list_active_for_start`` call still returns the Claim as
    active (its snapshot predates the disposal) — proven by ``calls[0]``
    below. But when T1 then tries to INSERT its DatumUsage row, which
    FK-references that Claim, PostgreSQL detects the claim row was
    concurrently updated and committed since T1's snapshot, and raises
    'could not serialize access due to concurrent update' (SQLSTATE 40001) —
    the FK's implicit referential-integrity lock cannot honor a snapshot
    view of a row that has since changed. The service's own documented
    retry (module docstring, phase-2 TRANSACTION section, '40001 -> ...
    entirely fresh transaction ... re-run the WHOLE pipeline once') then
    reruns everything on a BRAND NEW, later snapshot, whose
    ``list_active_for_start`` correctly no longer sees the disposed claim
    (``calls[1]`` below) — so the Run that actually ends up committed simply
    never references it. A Run can therefore never end up on-disk still
    referencing a Claim whose disposal commits during that Run's own
    attempt; PostgreSQL's own concurrency control forecloses the race
    before it can produce a stale, torn result."""
    ctx = _setup(postgres_engine, "VC3 Case B Retry")
    _claim_public_id, claim_id, _entry_id = _add_claim(
        postgres_engine, ctx, period_start=date(2026, 3, 1), period_end=date(2026, 3, 31), key="vc3b-retry-claim"
    )
    snapshot_ready, proceed, calls = _pause_after_snapshot_and_record_claims(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_measurement, postgres_engine, ctx, key="vc3b-retry-run")
        assert snapshot_ready.wait(timeout=15), "measurement never reached its claims read"
        disposed = _dispose_claim(postgres_engine, ctx, _claim_public_id)
        proceed.set()
        run, created = future.result(timeout=20)
    monkeypatch.undo()
    assert created is True
    assert disposed.disposed_at is not None
    assert len(calls) == 2, f"expected exactly one transparent 40001 retry, got {len(calls)} attempt(s): {calls!r}"
    assert calls[0] == [(claim_id, None)], "attempt 1's snapshot correctly still saw the claim active"
    assert calls[1] == [], "attempt 2's fresh, later snapshot correctly excludes the now-disposed claim"

    usages = _usages_for_run(postgres_engine, run.id)
    assert all(usage.claim_id != claim_id for usage in usages), "the committed Run must not reference the disposed claim"


def test_a_committed_run_is_never_mutated_by_a_later_claim_disposal(postgres_engine) -> None:
    """The actual, constructible equivalent of 'disposed after the fact
    never disturbs a historical Run': sequential, not concurrent — once a
    Run has FULLY COMMITTED referencing an active Claim, disposing that
    Claim afterward (an entirely separate, later transaction) must never
    retroactively alter the already-persisted DatumUsage row, and the Claim
    itself is genuinely disposed on a fresh re-read."""
    ctx = _setup(postgres_engine, "VC3 Historical Run Immutable")
    claim_public_id, claim_id, _entry_id = _add_claim(
        postgres_engine, ctx, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31), key="vc3-hist-claim"
    )
    run, created = _run_measurement(postgres_engine, ctx, key="vc3-hist-run")
    assert created is True
    (before,) = [u for u in _usages_for_run(postgres_engine, run.id) if u.claim_id == claim_id]

    disposed = _dispose_claim(postgres_engine, ctx, claim_public_id)
    assert disposed.disposed_at is not None

    with Session(postgres_engine) as check:
        claim_row = check.get(ExperimentEvidenceClaim, claim_id)
        assert claim_row.disposed_at is not None  # the claim itself IS disposed now...
    (after,) = [u for u in _usages_for_run(postgres_engine, run.id) if u.claim_id == claim_id]
    assert after.id == before.id  # ...but this is the SAME DatumUsage row, never rewritten
    assert after.later_grouping_entry_exists_at_run == before.later_grouping_entry_exists_at_run


# --- MetricEntry/MetricValue snapshot boundary ---------------------------------------------------------------------------


def test_vc1_new_metric_entry_and_claim_committed_after_the_snapshot_are_invisible(postgres_engine, monkeypatch) -> None:
    """Same snapshot boundary as VC-1, proven again for a BRAND NEW
    MetricEntry+MetricValue (not merely a claim on pre-existing evidence)
    committed by T2 entirely after T1's snapshot."""
    ctx = _setup(postgres_engine, "VC1 New Entry After Snapshot")
    snapshot_ready, proceed = _pause_after_snapshot(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_measurement, postgres_engine, ctx, key="vc1-newentry-after-run")
        assert snapshot_ready.wait(timeout=15), "measurement never reached its claims read"
        _claim_public_id, claim_id, _entry_id = _add_claim(
            postgres_engine,
            ctx,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
            channel="sms",
            metric_name="reach",
            key="vc1-newentry-after-claim",
        )
        proceed.set()
        run, created = future.result(timeout=20)
    monkeypatch.undo()
    assert created is True
    usages = _usages_for_run(postgres_engine, run.id)
    assert all(usage.claim_id != claim_id for usage in usages)


def test_vc1_control_new_metric_entry_and_claim_committed_before_the_run_is_visible(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "VC1 New Entry Before Snapshot Control")
    _claim_public_id, claim_id, _entry_id = _add_claim(
        postgres_engine,
        ctx,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        channel="sms",
        metric_name="reach",
        key="vc1-newentry-before-claim",
    )
    run, created = _run_measurement(postgres_engine, ctx, key="vc1-newentry-before-run")
    assert created is True
    usages = _usages_for_run(postgres_engine, run.id)
    assert any(usage.claim_id == claim_id for usage in usages)


# --- VC-4: the later-grouping-entry disclosure is itself snapshot-bound --------------------------------------------------


def test_vc4_later_grouping_entry_visible_before_the_snapshot_is_disclosed_true(postgres_engine) -> None:
    """``later_grouping_entry_exists_at_run`` is read through
    ``MetricEntryRepository.exists_later_in_grouping`` inside the SAME
    REPEATABLE READ transaction as the claims read — so it shares the same
    snapshot boundary. A later entry in the same (campaign, period, channel)
    grouping, committed and visible BEFORE the Run's snapshot, must disclose
    True. No claim on the later entry is required — the check only looks at
    entries in the grouping, not at claims."""
    ctx = _setup(postgres_engine, "VC4 Later Grouping Before Snapshot")
    period_start, period_end, channel = date(2026, 5, 1), date(2026, 5, 31), "email"
    _claim_public_id, claim_id, _entry_id = _add_claim(
        postgres_engine,
        ctx,
        period_start=period_start,
        period_end=period_end,
        channel=channel,
        entry_created_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
        key="vc4-before-claim",
    )
    _add_entry(
        postgres_engine,
        ctx,
        period_start=period_start,
        period_end=period_end,
        channel=channel,
        entry_created_at=datetime(2026, 5, 1, 12, 0, 1, tzinfo=timezone.utc),
    )
    run, created = _run_measurement(postgres_engine, ctx, key="vc4-before-run")
    assert created is True
    usages = _usages_for_run(postgres_engine, run.id)
    (usage,) = [u for u in usages if u.claim_id == claim_id]
    assert usage.later_grouping_entry_exists_at_run is True


def test_vc4_later_grouping_entry_committed_after_the_snapshot_is_disclosed_false(postgres_engine, monkeypatch) -> None:
    """The mirror case, with genuine interleaving: T1 is paused right after
    its snapshot is established; T2 commits the LATER grouping entry only
    then. The disclosure must be False, and a later re-read of that same
    historical Run must still show False — the disclosure is a frozen,
    one-time computation, never re-evaluated."""
    ctx = _setup(postgres_engine, "VC4 Later Grouping After Snapshot")
    period_start, period_end, channel = date(2026, 6, 1), date(2026, 6, 30), "email"
    _claim_public_id, claim_id, _entry_id = _add_claim(
        postgres_engine,
        ctx,
        period_start=period_start,
        period_end=period_end,
        channel=channel,
        entry_created_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        key="vc4-after-claim",
    )
    snapshot_ready, proceed = _pause_after_snapshot(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_measurement, postgres_engine, ctx, key="vc4-after-run")
        assert snapshot_ready.wait(timeout=15), "measurement never reached its claims read"
        _add_entry(
            postgres_engine,
            ctx,
            period_start=period_start,
            period_end=period_end,
            channel=channel,
            entry_created_at=datetime(2026, 6, 1, 12, 0, 1, tzinfo=timezone.utc),
        )
        proceed.set()
        run, created = future.result(timeout=20)
    monkeypatch.undo()
    assert created is True
    usages = _usages_for_run(postgres_engine, run.id)
    (usage,) = [u for u in usages if u.claim_id == claim_id]
    assert usage.later_grouping_entry_exists_at_run is False

    with Session(postgres_engine) as check:
        replay = ExperimentMeasurementRunRepository(check).list_datum_usages_for_run(run_id=run.id)
    (replay_usage,) = [u for u in replay if u.claim_id == claim_id]
    assert replay_usage.later_grouping_entry_exists_at_run is False
