"""Forced real-PostgreSQL overlap and real-rollback tests for Governed
Execution Start (frozen Design Freeze), using the same ``pg_blocking_pids()``
technique as the Definition/Variant/Contract/Authorization concurrency
suites: every contending operation is observed genuinely blocked by
PostgreSQL on a row lock held open by a separate, independent session before
that lock is released — no sleep-based ordering, no serialized test client.
Each operation runs in its own independent DB session/backend.

The Start writer's lock root is the EXPERIMENT row (Experiment, then the
Authorization row) — it never takes the Strategy lock (frozen S1), so the
overlap holder here holds the Experiment row, and the Start<->Strategy
revision independence is proven by a Start COMPLETING while another session
holds the Strategy row lock. The atomicity tests inject a REAL database
constraint violation and verify the whole aggregate rolled back in a fresh
session — no mock-only proof.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from time import monotonic, sleep

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.campaigns.repository import CampaignRepository
from app.core.api_errors import ApiError
from app.strategy.execution_authorization_service import ExperimentExecutionAuthorizationService
from app.strategy.execution_start_service import ExperimentExecutionStartService
from app.strategy.models import (
    ExecutionAuthorization,
    ExecutionStartAttestation,
    Experiment,
    ExperimentVariant,
    MeasurementContractVersion,
    Strategy,
)
from tests.test_execution_authorization_concurrency import (
    _auth_op,
    _contract_revision_op,
    _revoke_op,
    _setup,
    _strategy_revision_op,
    _variant_op,
)
from tests.test_experiment_definition_concurrency import _wait_blocked_by

pytestmark = pytest.mark.postgres


def _authorized_ctx(engine, name: str) -> dict:
    """The minimum authorizable state, already AUTHORIZED (one active Authorization, not started)."""
    ctx = _setup(engine, name)
    with Session(engine, expire_on_commit=False) as session:
        authorization, _snapshot, _created = _auth_op(ctx, key="seed-auth")(session)
        ctx["authorization"] = authorization.public_id
        ctx["authorization_id"] = authorization.id
        ctx["started_at"] = authorization.created_at + timedelta(seconds=1)
    return ctx


def _start_op(ctx, *, key, authorization_public_id=None):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentExecutionStartService(session).start(
            campaign=campaign, experiment_public_id=ctx["experiment"],
            authorization_public_id=authorization_public_id or ctx["authorization"],
            client_request_id=key, started_at=ctx["started_at"], actor_user_id=ctx["actor"],
        )

    return operation


def _starts(engine, ctx) -> list[ExecutionStartAttestation]:
    with Session(engine) as check:
        rows = check.execute(
            select(ExecutionStartAttestation)
            .join(ExecutionAuthorization, ExecutionAuthorization.id == ExecutionStartAttestation.authorization_id)
            .where(ExecutionAuthorization.experiment_id == ctx["experiment_id"])
        ).scalars().all()
        check.expunge_all()
        return list(rows)


def _start_events(engine, ctx) -> int:
    with Session(engine) as check:
        return check.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.experiment_id == ctx["experiment_id"],
                AuditEvent.event_type == "strategy.execution_start.attested",
            )
        )


def _authorizations(engine, ctx) -> list[ExecutionAuthorization]:
    with Session(engine) as check:
        rows = check.execute(
            select(ExecutionAuthorization)
            .where(ExecutionAuthorization.experiment_id == ctx["experiment_id"])
            .order_by(ExecutionAuthorization.created_at, ExecutionAuthorization.id)
        ).scalars().all()
        check.expunge_all()
        return list(rows)


def _code(result) -> str | None:
    return result.code if isinstance(result, ApiError) else None


def _holder_then_worker_on_experiment(engine, ctx, holder_op, worker_op):
    """The holder takes the EXPERIMENT row lock; the worker is observed genuinely BLOCKED by
    PostgreSQL on it; only then does the holder run its own operation and commit."""
    pids: list[int] = []

    def worker():
        with Session(engine, expire_on_commit=False) as session:
            pids.append(session.scalar(text("select pg_backend_pid()")))
            try:
                return worker_op(session)
            except ApiError as exc:
                session.rollback()
                return exc

    with Session(engine, expire_on_commit=False) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.scalar(select(Experiment).where(Experiment.id == ctx["experiment_id"]).with_for_update())
        holder_pid = holder.scalar(text("select pg_backend_pid()"))
        future = pool.submit(worker)
        try:
            deadline = monotonic() + 15
            while not pids and monotonic() < deadline:
                sleep(0.01)
            assert pids, "worker never started"
            _wait_blocked_by(engine, waiting_pids=list(pids), holding_pid=holder_pid)
            try:
                holder_result = holder_op(holder)
            except ApiError as exc:
                holder.rollback()
                holder_result = exc
        finally:
            holder.rollback()
        outcome = future.result(timeout=20)
    return holder_result, outcome


def _race_on_experiment(engine, ctx, operations):
    """Runs ``operations`` concurrently in independent sessions, all forced to be blocked on the
    Experiment row lock held by a separate holder session before that lock is released."""
    from threading import Lock

    pids: list[int] = []
    guard = Lock()
    results: list[object] = [None] * len(operations)

    def worker(index: int) -> object:
        with Session(engine, expire_on_commit=False) as session:
            with guard:
                pids.append(session.scalar(text("select pg_backend_pid()")))
            try:
                return operations[index](session)
            except ApiError as exc:
                session.rollback()
                return exc

    with Session(engine, expire_on_commit=False) as holder, ThreadPoolExecutor(max_workers=len(operations)) as pool:
        holder.scalar(select(Experiment).where(Experiment.id == ctx["experiment_id"]).with_for_update())
        holder_pid = holder.scalar(text("select pg_backend_pid()"))
        futures = [pool.submit(worker, i) for i in range(len(operations))]
        try:
            deadline = monotonic() + 15
            while len(pids) < len(operations) and monotonic() < deadline:
                sleep(0.01)
            assert len(set(pids)) == len(operations) and holder_pid not in pids
            _wait_blocked_by(engine, waiting_pids=list(pids), holding_pid=holder_pid)
        except BaseException:
            holder.rollback()
            raise
        holder.commit()
        for index, future in enumerate(futures):
            results[index] = future.result(timeout=20)
    return results


# --- the Start genuinely serializes on the Experiment row -----------------------------------------------------


def test_a_start_write_genuinely_blocks_on_the_experiment_row_lock(postgres_engine) -> None:
    ctx = _authorized_ctx(postgres_engine, "Start Experiment Lock")
    pids: list[int] = []

    def operation():
        with Session(postgres_engine, expire_on_commit=False) as session:
            pids.append(session.scalar(text("select pg_backend_pid()")))
            return _start_op(ctx, key="explock")(session)

    with Session(postgres_engine, expire_on_commit=False) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.scalar(select(Experiment).where(Experiment.id == ctx["experiment_id"]).with_for_update())
        holder_pid = holder.scalar(text("select pg_backend_pid()"))
        future = pool.submit(operation)
        try:
            deadline = monotonic() + 15
            while not pids and monotonic() < deadline:
                sleep(0.01)
            _wait_blocked_by(postgres_engine, waiting_pids=list(pids), holding_pid=holder_pid)
        finally:
            holder.rollback()
        _auth, _start, created = future.result(timeout=20)
    assert created is True


# --- Start <-> Start ---------------------------------------------------------------------------------------------


def test_simultaneous_same_key_is_one_creation_and_one_replay(postgres_engine) -> None:
    ctx = _authorized_ctx(postgres_engine, "Start Same Key")
    results = _race_on_experiment(postgres_engine, ctx, [_start_op(ctx, key="same"), _start_op(ctx, key="same")])
    assert all(isinstance(r, tuple) for r in results), results
    assert sorted(created for _a, _s, created in results) == [False, True]
    assert results[0][1].id == results[1][1].id
    assert len(_starts(postgres_engine, ctx)) == 1 and _start_events(postgres_engine, ctx) == 1


def test_simultaneous_different_keys_are_one_creation_and_one_already_started(postgres_engine) -> None:
    ctx = _authorized_ctx(postgres_engine, "Start Different Keys")
    results = _race_on_experiment(postgres_engine, ctx, [_start_op(ctx, key="key-one"), _start_op(ctx, key="key-two")])
    created = [r for r in results if isinstance(r, tuple)]
    errors = [r for r in results if isinstance(r, ApiError)]
    assert len(created) == 1 and created[0][2] is True, results
    assert [e.code for e in errors] == ["EXECUTION_START_ALREADY_STARTED"] and errors[0].status_code == 409
    assert len(_starts(postgres_engine, ctx)) == 1 and _start_events(postgres_engine, ctx) == 1  # never a duplicate


# --- Start <-> Revocation ------------------------------------------------------------------------------------------


def test_start_commits_first_then_revoke_leaves_the_start_historical(postgres_engine) -> None:
    ctx = _authorized_ctx(postgres_engine, "Start Then Revoke")
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _start_op(ctx, key="s-1"), _revoke_op(ctx)
    )
    assert isinstance(holder_result, tuple) and holder_result[2] is True
    assert isinstance(outcome, ExecutionAuthorization) and outcome.revoked_at is not None
    assert len(_starts(postgres_engine, ctx)) == 1  # revocation never deletes or invalidates the start
    assert _authorizations(postgres_engine, ctx)[0].revoked_at is not None


def test_revoke_commits_first_then_the_start_is_refused_not_active(postgres_engine) -> None:
    ctx = _authorized_ctx(postgres_engine, "Revoke Then Start")
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _revoke_op(ctx), _start_op(ctx, key="s-1")
    )
    assert isinstance(holder_result, ExecutionAuthorization)
    assert _code(outcome) == "EXECUTION_START_AUTHORIZATION_NOT_ACTIVE"
    assert _starts(postgres_engine, ctx) == [] and _start_events(postgres_engine, ctx) == 0


@pytest.mark.parametrize("attempt", range(3))
def test_unordered_start_vs_revocation_is_coherent(postgres_engine, attempt: int) -> None:
    ctx = _authorized_ctx(postgres_engine, f"Start Revoke Unordered {attempt}")
    start_result, revoke_result = _race_on_experiment(
        postgres_engine, ctx, [_start_op(ctx, key="s-1"), _revoke_op(ctx)]
    )
    assert isinstance(revoke_result, ExecutionAuthorization)  # revocation always succeeds exactly once
    starts = _starts(postgres_engine, ctx)
    if isinstance(start_result, tuple):
        assert start_result[2] is True and len(starts) == 1
    else:
        assert _code(start_result) == "EXECUTION_START_AUTHORIZATION_NOT_ACTIVE" and starts == []
    assert _authorizations(postgres_engine, ctx)[0].revoked_at is not None
    assert _start_events(postgres_engine, ctx) == len(starts)


# --- Start <-> Reauthorization ---------------------------------------------------------------------------------------


def test_unstarted_reauthorization_commits_first_then_the_old_start_is_refused(postgres_engine) -> None:
    ctx = _authorized_ctx(postgres_engine, "Reauth Then Start")
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _auth_op(ctx, key="re-auth", design="Second."), _start_op(ctx, key="s-1")
    )
    assert isinstance(holder_result, tuple) and holder_result[2] is True  # unstarted auto-supersession still works
    assert _code(outcome) == "EXECUTION_START_AUTHORIZATION_NOT_ACTIVE"  # A is now revoked/superseded
    assert _starts(postgres_engine, ctx) == []


def test_start_commits_first_then_authorize_is_refused_active_started(postgres_engine) -> None:
    ctx = _authorized_ctx(postgres_engine, "Start Then Reauth")
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _start_op(ctx, key="s-1"), _auth_op(ctx, key="re-auth", design="Second.")
    )
    assert isinstance(holder_result, tuple) and holder_result[2] is True
    assert _code(outcome) == "EXECUTION_AUTHORIZATION_ACTIVE_STARTED"
    rows = _authorizations(postgres_engine, ctx)
    assert len(rows) == 1 and rows[0].revoked_at is None  # no implicit revocation, no silent supersession
    assert len(_starts(postgres_engine, ctx)) == 1


@pytest.mark.parametrize("attempt", range(3))
def test_unordered_start_vs_reauthorization_has_exactly_one_winner(postgres_engine, attempt: int) -> None:
    ctx = _authorized_ctx(postgres_engine, f"Start Reauth Unordered {attempt}")
    start_result, auth_result = _race_on_experiment(
        postgres_engine, ctx, [_start_op(ctx, key="s-1"), _auth_op(ctx, key="re-auth", design="Second.")]
    )
    starts = _starts(postgres_engine, ctx)
    rows = _authorizations(postgres_engine, ctx)
    if isinstance(start_result, tuple):  # start won: authorize refused, A untouched and active
        assert _code(auth_result) == "EXECUTION_AUTHORIZATION_ACTIVE_STARTED"
        assert len(starts) == 1 and len(rows) == 1 and rows[0].revoked_at is None
    else:  # authorize won: the old start was refused, the old Authorization is superseded
        assert _code(start_result) == "EXECUTION_START_AUTHORIZATION_NOT_ACTIVE"
        assert isinstance(auth_result, tuple) and starts == [] and len(rows) == 2
        assert rows[0].revoked_at is not None and rows[1].revoked_at is None
    assert sum(1 for r in rows if r.revoked_at is None) == 1


# --- Start <-> Contract revision ---------------------------------------------------------------------------------------


def test_start_commits_first_then_a_contract_revision_is_frozen_by_the_start(postgres_engine) -> None:
    ctx = _authorized_ctx(postgres_engine, "Start Then Contract")
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _start_op(ctx, key="s-1"), _contract_revision_op(ctx)
    )
    assert isinstance(holder_result, tuple) and holder_result[2] is True
    assert _code(outcome) == "MEASUREMENT_CONTRACT_FROZEN_BY_EXECUTION_START"  # the start code wins, deterministically
    with Session(postgres_engine) as check:
        assert check.scalar(
            select(func.count()).select_from(MeasurementContractVersion).where(
                MeasurementContractVersion.experiment_id == ctx["experiment_id"]
            )
        ) == 1


def test_a_contract_revision_that_commits_first_leaves_the_old_authorization_unstartable(postgres_engine) -> None:
    """A revision can only commit when no Authorization is active (the existing freeze), so the
    revision-first ordering necessarily follows a revocation; the old Authorization can never be started."""
    ctx = _authorized_ctx(postgres_engine, "Contract Then Start")
    with Session(postgres_engine) as session:
        _revoke_op(ctx)(session)
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _contract_revision_op(ctx), _start_op(ctx, key="s-1")
    )
    assert isinstance(holder_result, tuple) and holder_result[3] is True and holder_result[0].version == 2
    assert _code(outcome) == "EXECUTION_START_AUTHORIZATION_NOT_ACTIVE"
    assert _starts(postgres_engine, ctx) == []


# --- Start <-> Variant declaration ---------------------------------------------------------------------------------------


def test_start_commits_first_then_a_variant_declaration_is_frozen(postgres_engine) -> None:
    ctx = _authorized_ctx(postgres_engine, "Start Then Variant")
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _start_op(ctx, key="s-1"), _variant_op(ctx, key="late-v", label="Late")
    )
    assert isinstance(holder_result, tuple) and holder_result[2] is True
    assert _code(outcome) == "EXPERIMENT_VARIANT_FROZEN_BY_EXECUTION_START"
    with Session(postgres_engine) as check:
        assert check.scalar(
            select(func.count()).select_from(ExperimentVariant).where(ExperimentVariant.experiment_id == ctx["experiment_id"])
        ) == 1


def test_variant_commits_first_then_the_start_is_stale(postgres_engine) -> None:
    ctx = _authorized_ctx(postgres_engine, "Variant Then Start")
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _variant_op(ctx, key="late-v", label="Late"), _start_op(ctx, key="s-1")
    )
    assert isinstance(holder_result, tuple) and holder_result[2] is True
    assert _code(outcome) == "EXECUTION_START_AUTHORIZATION_STALE"  # a pre-lock comparison would have passed
    assert _starts(postgres_engine, ctx) == []
    with Session(postgres_engine) as check:  # the historical Authorization snapshot is never refreshed
        assert check.scalar(
            text("select count(*) from execution_authorization_variants where authorization_id = :a"),
            {"a": ctx["authorization_id"]},
        ) == 1


@pytest.mark.parametrize("attempt", range(3))
def test_unordered_start_vs_variant_declaration_has_exactly_one_winner(postgres_engine, attempt: int) -> None:
    ctx = _authorized_ctx(postgres_engine, f"Start Variant Unordered {attempt}")
    start_result, variant_result = _race_on_experiment(
        postgres_engine, ctx, [_start_op(ctx, key="s-1"), _variant_op(ctx, key="late-v", label="Late")]
    )
    starts = _starts(postgres_engine, ctx)
    with Session(postgres_engine) as check:
        variants = check.scalar(
            select(func.count()).select_from(ExperimentVariant).where(ExperimentVariant.experiment_id == ctx["experiment_id"])
        )
    if isinstance(start_result, tuple):
        assert _code(variant_result) == "EXPERIMENT_VARIANT_FROZEN_BY_EXECUTION_START"
        assert len(starts) == 1 and variants == 1
    else:
        assert _code(start_result) == "EXECUTION_START_AUTHORIZATION_STALE"
        assert isinstance(variant_result, tuple) and starts == [] and variants == 2


# --- Start <-> Strategy revision (S1 independence) ---------------------------------------------------------------------------


def test_a_start_is_not_blocked_by_the_strategy_lock_and_a_revision_still_commits(postgres_engine) -> None:
    ctx = _authorized_ctx(postgres_engine, "Start Strategy Independent")
    pids: list[int] = []

    def worker():
        with Session(postgres_engine, expire_on_commit=False) as session:
            pids.append(session.scalar(text("select pg_backend_pid()")))
            return _start_op(ctx, key="s-1")(session)

    with Session(postgres_engine, expire_on_commit=False) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.scalar(select(Strategy).where(Strategy.id == ctx["strategy_id"]).with_for_update())
        holder_pid = holder.scalar(text("select pg_backend_pid()"))
        future = pool.submit(worker)
        try:
            # The Start must COMPLETE while the Strategy row lock is still held by the other session.
            _authorization, start, created = future.result(timeout=15)
            with postgres_engine.connect() as observer:
                blockers = observer.execute(text("select pg_blocking_pids(:p)"), {"p": pids[0]}).scalar_one()
            assert holder_pid not in blockers  # it was never waiting on the Strategy lock
        finally:
            holder.rollback()
    assert created is True and start.public_id.startswith("EXS-")
    # A Strategy revision afterwards commits without deadlock; the started Authorization stays active (S1).
    with Session(postgres_engine) as session:
        assert _strategy_revision_op(ctx)(session) == "ok"
    with Session(postgres_engine) as check:
        assert check.scalar(select(func.count()).select_from(Strategy).where(Strategy.campaign_id == select(Strategy.campaign_id).where(Strategy.id == ctx["strategy_id"]).scalar_subquery())) == 2
    rows = _authorizations(postgres_engine, ctx)
    assert len(rows) == 1 and rows[0].revoked_at is None and len(_starts(postgres_engine, ctx)) == 1


def test_a_strategy_revision_committing_first_does_not_stop_the_start(postgres_engine) -> None:
    ctx = _authorized_ctx(postgres_engine, "Strategy Then Start")
    with Session(postgres_engine) as session:
        assert _strategy_revision_op(ctx)(session) == "ok"
    with Session(postgres_engine, expire_on_commit=False) as session:
        _a, start, created = _start_op(ctx, key="s-1")(session)
    assert created is True and len(_starts(postgres_engine, ctx)) == 1


# --- atomicity (real rollback, injected real constraint violation) ---------------------------------------------------------------


def _fail_start_audit(monkeypatch) -> None:
    """Makes the start's audit insert violate a real FK, after the Start row was already flushed
    inside the same transaction."""
    real_record = AuditEventRepository.record

    def record(self, **kwargs):
        if kwargs.get("event_type") == "strategy.execution_start.attested":
            kwargs["execution_start_attestation_id"] = uuid.uuid4()  # nonexistent -> real FK violation
        return real_record(self, **kwargs)

    monkeypatch.setattr(AuditEventRepository, "record", record)


def test_a_failing_audit_write_rolls_back_the_whole_start(postgres_engine, monkeypatch) -> None:
    ctx = _authorized_ctx(postgres_engine, "Start Atomic")
    with Session(postgres_engine) as check:
        before = (
            check.scalar(select(func.count()).select_from(ExperimentVariant)),
            check.scalar(select(func.count()).select_from(MeasurementContractVersion)),
        )
    _fail_start_audit(monkeypatch)
    with Session(postgres_engine) as session:
        with pytest.raises(IntegrityError):  # an unexpected IntegrityError is re-raised, never translated
            _start_op(ctx, key="doomed")(session)
    monkeypatch.undo()
    assert _starts(postgres_engine, ctx) == []  # 0 partial Start rows
    assert _start_events(postgres_engine, ctx) == 0  # 0 orphan audit events
    rows = _authorizations(postgres_engine, ctx)
    assert len(rows) == 1 and rows[0].revoked_at is None and rows[0].revoked_reason is None  # lifecycle untouched
    with Session(postgres_engine) as check:  # Contract and Variant state untouched
        assert (
            check.scalar(select(func.count()).select_from(ExperimentVariant)),
            check.scalar(select(func.count()).select_from(MeasurementContractVersion)),
        ) == before
    # The key was never consumed: a clean retry now succeeds.
    with Session(postgres_engine, expire_on_commit=False) as session:
        assert _start_op(ctx, key="doomed")(session)[2] is True


def test_the_committed_start_event_actor_is_the_user(postgres_engine) -> None:
    ctx = _authorized_ctx(postgres_engine, "Start Actor")
    with Session(postgres_engine) as session:
        _start_op(ctx, key="s-1")(session)
    with Session(postgres_engine) as check:
        rows = check.execute(
            select(AuditEvent.actor_type, AuditEvent.actor_user_id).where(
                AuditEvent.experiment_id == ctx["experiment_id"],
                AuditEvent.event_type == "strategy.execution_start.attested",
            )
        ).all()
    assert rows == [(ActorType.USER, ctx["actor"])]
