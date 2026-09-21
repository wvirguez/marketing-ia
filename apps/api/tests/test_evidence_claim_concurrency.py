"""Forced real-PostgreSQL overlap and real-rollback tests for Experiment
Evidence Binding (frozen Design Freeze), using the same ``pg_blocking_pids()``
technique as the Start/Authorization/Contract concurrency suites: every
contending operation is observed genuinely blocked by PostgreSQL on a row lock
held open by a separate, independent session before that lock is released —
no sleep-based ordering, no serialized test client. Each operation runs in its
own independent DB session/backend.

The claim writer's lock root is the EXPERIMENT row (Experiment, then the claim
row) — never a Strategy/Hypothesis lock and never an Authorization
``FOR UPDATE``. The Experiment lock is required for LOCK ORDER, not for
semantic validity: without it a claim's audit FK (KEY SHARE on the Experiment)
would wait on ``revoke`` while ``revoke`` (Experiment lock, then Authorization
``FOR UPDATE``) waits on the claim's own KEY SHARE on the Authorization — a
deadlock (the EXSTART-IMPL-OBS-1 class). ``test_claim_and_revoke_interleave_*``
forces exactly that interleaving; the mutation test proves the harness DOES
detect the deadlock when the Experiment lock is removed.

The atomicity tests inject a REAL database constraint violation after the
primary write and verify the whole unit rolled back in a fresh session.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier, Event
from time import monotonic, sleep

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.campaigns.repository import CampaignRepository
from app.core.api_errors import ApiError
from app.measurement.models import MetricSource
from app.measurement.service import MeasurementService
from app.strategy.experiment_evidence_claim_service import ExperimentEvidenceClaimService
from app.strategy.models import ExecutionAuthorization, ExperimentEvidenceClaim, Strategy
from app.strategy.repository import ExperimentEvidenceClaimRepository, MeasurementContractRepository
from tests.evidenceclaimtest import make_entry
from tests.test_execution_authorization_concurrency import _auth_op, _revoke_op
from tests.test_execution_start_concurrency import (
    _authorized_ctx,
    _holder_then_worker_on_experiment,
    _race_on_experiment,
    _start_op,
)
from tests.test_experiment_definition_concurrency import _wait_blocked_by

pytestmark = pytest.mark.postgres

CLAIMED = "strategy.evidence_claim.claimed"
DISPOSED = "strategy.evidence_claim.disposed"


def _started_ctx(engine, name: str) -> dict:
    """A STARTED attempt (committed) plus four committed MetricEntries and the Contract's signal."""
    ctx = _authorized_ctx(engine, name)
    with Session(engine, expire_on_commit=False) as session:
        _authorization, start, _created = _start_op(ctx, key="seed-start")(session)
        ctx["start"], ctx["start_id"] = start.public_id, start.id
        signal = MeasurementContractRepository(session).list_signals_for_version(contract_version_id=ctx["contract_id"])[0]
        ctx["signal"], ctx["signal_id"] = signal.public_id, signal.id
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        entries = [make_entry(session, campaign, values={"clicks": Decimal("50"), "reach": Decimal("9")}) for _ in range(4)]
        session.commit()
        ctx["entries"] = [entry.public_id for entry in entries]
        ctx["entry_ids"] = [entry.id for entry in entries]
    return ctx


def _claim_op(ctx, *, key, entry=0, metric_name="clicks", start=None, drop_experiment_lock=False):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        service = ExperimentEvidenceClaimService(session)
        if drop_experiment_lock:  # MUTATION ONLY: the claim writer stops taking the Experiment row lock.
            real = service.experiments.get_by_id
            service.experiments.get_by_id = lambda experiment_id, *, for_update=False: real(experiment_id)
        return service.create(
            campaign=campaign, experiment_public_id=ctx["experiment"], start_public_id=start or ctx["start"],
            client_request_id=key, required_signal_public_id=ctx["signal"], metric_entry_public_id=ctx["entries"][entry],
            metric_name=metric_name, actor_user_id=ctx["actor"],
        )

    return operation


def _dispose_op(ctx, claim_public_id, *, reason="No longer intended."):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentEvidenceClaimService(session).dispose(
            campaign=campaign, experiment_public_id=ctx["experiment"], start_public_id=ctx["start"],
            claim_public_id=claim_public_id, reason=reason, actor_user_id=ctx["actor"],
        )

    return operation


def _correction_op(ctx, *, values=None):
    """A real MetricEntry correction through the production service: it takes NO Experiment lock."""

    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return MeasurementService(session).record_metric_entry(
            campaign=campaign, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), channel="email",
            source=MetricSource.MANUAL, client_request_id=uuid.uuid4().hex,
            metric_values=values or {"clicks": Decimal("99")}, is_correction=True, actor_user_id=ctx["actor"],
        )

    return operation


def _seed_claim(engine, ctx, *, key="seed-claim", entry=0, metric_name="clicks"):
    with Session(engine, expire_on_commit=False) as session:
        claim, _created = _claim_op(ctx, key=key, entry=entry, metric_name=metric_name)(session)
        return claim


def _claims(engine, ctx) -> list[ExperimentEvidenceClaim]:
    with Session(engine) as check:
        rows = check.execute(
            select(ExperimentEvidenceClaim)
            .where(ExperimentEvidenceClaim.experiment_id == ctx["experiment_id"])
            .order_by(ExperimentEvidenceClaim.created_at, ExperimentEvidenceClaim.id)
        ).scalars().all()
        check.expunge_all()
        return list(rows)


def _active(rows) -> list[ExperimentEvidenceClaim]:
    return [row for row in rows if row.disposed_at is None]


def _events(engine, ctx, event_type) -> int:
    with Session(engine) as check:
        return check.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.experiment_id == ctx["experiment_id"], AuditEvent.event_type == event_type
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


# --- the claim genuinely serializes on the Experiment row --------------------------------------------------------


def test_a_claim_write_genuinely_blocks_on_the_experiment_row_lock(postgres_engine) -> None:
    ctx = _started_ctx(postgres_engine, "Claim Experiment Lock")
    pids: list[int] = []

    def operation():
        with Session(postgres_engine, expire_on_commit=False) as session:
            pids.append(session.scalar(text("select pg_backend_pid()")))
            return _claim_op(ctx, key="explock")(session)

    with Session(postgres_engine, expire_on_commit=False) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        from app.strategy.models import Experiment

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
        _claim, created = future.result(timeout=20)
    assert created is True


def test_a_disposal_genuinely_blocks_on_the_experiment_row_lock(postgres_engine) -> None:
    """Frozen lock order for disposal: the Experiment row FIRST, then the claim row. Being blocked is not enough
    (the audit insert's own KEY SHARE on the Experiment would also block a writer that skipped the explicit lock),
    so the ORDER is proven: while the disposal waits on the Experiment, it must NOT yet hold the claim row."""
    ctx = _started_ctx(postgres_engine, "Dispose Experiment Lock")
    seeded = _seed_claim(postgres_engine, ctx)
    pids: list[int] = []

    def operation():
        with Session(postgres_engine, expire_on_commit=False) as session:
            pids.append(session.scalar(text("select pg_backend_pid()")))
            return _dispose_op(ctx, seeded.public_id)(session)

    with Session(postgres_engine, expire_on_commit=False) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        from app.strategy.models import Experiment

        holder.scalar(select(Experiment).where(Experiment.id == ctx["experiment_id"]).with_for_update())
        holder_pid = holder.scalar(text("select pg_backend_pid()"))
        future = pool.submit(operation)
        try:
            deadline = monotonic() + 15
            while not pids and monotonic() < deadline:
                sleep(0.01)
            _wait_blocked_by(postgres_engine, waiting_pids=list(pids), holding_pid=holder_pid)
            with Session(postgres_engine) as probe:
                # NOWAIT raises immediately if the waiting disposal already holds the claim row lock.
                probe.execute(
                    text("select id from experiment_evidence_claims where id = :id for update nowait"),
                    {"id": seeded.id},
                )
                probe.rollback()
        finally:
            holder.rollback()
        disposed = future.result(timeout=20)
    assert disposed.disposed_at is not None


def test_a_claim_is_not_blocked_by_the_strategy_lock(postgres_engine) -> None:
    """NO Strategy lock (and no Hypothesis lock): the claim COMPLETES while another session holds the Strategy row."""
    ctx = _started_ctx(postgres_engine, "Claim Strategy Independent")
    pids: list[int] = []

    def worker():
        with Session(postgres_engine, expire_on_commit=False) as session:
            pids.append(session.scalar(text("select pg_backend_pid()")))
            return _claim_op(ctx, key="s-indep")(session)

    with Session(postgres_engine, expire_on_commit=False) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.scalar(select(Strategy).where(Strategy.id == ctx["strategy_id"]).with_for_update())
        holder_pid = holder.scalar(text("select pg_backend_pid()"))
        future = pool.submit(worker)
        try:
            claim, created = future.result(timeout=15)  # completes while the Strategy row lock is still held
            with postgres_engine.connect() as observer:
                blockers = observer.execute(text("select pg_blocking_pids(:p)"), {"p": pids[0]}).scalar_one()
            assert holder_pid not in blockers
        finally:
            holder.rollback()
    assert created is True and claim.public_id.startswith("ECL-")


# --- create <-> create ---------------------------------------------------------------------------------------------


def test_simultaneous_same_key_same_material_is_one_creation_and_one_replay(postgres_engine) -> None:
    ctx = _started_ctx(postgres_engine, "Claim Same Key")
    results = _race_on_experiment(postgres_engine, ctx, [_claim_op(ctx, key="same"), _claim_op(ctx, key="same")])
    assert all(isinstance(r, tuple) for r in results), results
    assert sorted(created for _c, created in results) == [False, True]
    assert results[0][0].id == results[1][0].id
    assert len(_claims(postgres_engine, ctx)) == 1 and _events(postgres_engine, ctx, CLAIMED) == 1


def test_simultaneous_different_keys_same_material_is_one_creation_and_one_already_active(postgres_engine) -> None:
    ctx = _started_ctx(postgres_engine, "Claim Different Keys")
    results = _race_on_experiment(postgres_engine, ctx, [_claim_op(ctx, key="key-one"), _claim_op(ctx, key="key-two")])
    created = [r for r in results if isinstance(r, tuple)]
    errors = [r for r in results if isinstance(r, ApiError)]
    assert len(created) == 1 and created[0][1] is True, results
    assert [e.code for e in errors] == ["EVIDENCE_CLAIM_ALREADY_ACTIVE"] and errors[0].status_code == 409
    assert len(_claims(postgres_engine, ctx)) == 1 and _events(postgres_engine, ctx, CLAIMED) == 1  # never a duplicate


def test_simultaneous_claims_of_different_material_both_commit(postgres_engine) -> None:
    ctx = _started_ctx(postgres_engine, "Claim Different Material")
    results = _race_on_experiment(
        postgres_engine, ctx, [_claim_op(ctx, key="a", entry=0), _claim_op(ctx, key="b", entry=1, metric_name="reach")]
    )
    assert all(isinstance(r, tuple) and r[1] is True for r in results), results
    assert len(_claims(postgres_engine, ctx)) == 2 and _events(postgres_engine, ctx, CLAIMED) == 2


# --- the DB backstops behind the pre-checks (a lost race) ------------------------------------------------------------------


def test_a_lost_race_on_the_active_datum_index_is_translated_to_already_active(postgres_engine, monkeypatch) -> None:
    """The under-lock pre-check normally makes the partial unique index unreachable. If a race were ever lost the
    database still refuses the second active claim and the writer must translate it — never a raw 500."""
    ctx = _started_ctx(postgres_engine, "Claim Active Backstop")
    _seed_claim(postgres_engine, ctx)
    monkeypatch.setattr(ExperimentEvidenceClaimRepository, "get_active_for_material", lambda self, **kwargs: None)
    with Session(postgres_engine) as session:
        with pytest.raises(ApiError) as excinfo:
            _claim_op(ctx, key="other-key")(session)
    monkeypatch.undo()
    assert excinfo.value.code == "EVIDENCE_CLAIM_ALREADY_ACTIVE" and excinfo.value.status_code == 409
    assert len(_claims(postgres_engine, ctx)) == 1 and _events(postgres_engine, ctx, CLAIMED) == 1


def test_a_lost_race_on_the_client_key_is_translated_to_a_replay_of_the_winner(postgres_engine, monkeypatch) -> None:
    """A same-key, same-material retry that loses the race hits BOTH unique constraints; whichever PostgreSQL reports
    first, the retry must return the winner's claim (a replay), never ALREADY_ACTIVE (the EXSTART-IMPL-OBS-3 class)."""
    ctx = _started_ctx(postgres_engine, "Claim Key Backstop")
    seeded = _seed_claim(postgres_engine, ctx, key="k-1")
    real_replay = ExperimentEvidenceClaimService._replay
    calls = {"count": 0}

    def replay(self, **kwargs):
        calls["count"] += 1
        return None if calls["count"] <= 2 else real_replay(self, **kwargs)  # blind fast-path + post-lock lookups

    monkeypatch.setattr(ExperimentEvidenceClaimService, "_replay", replay)
    monkeypatch.setattr(ExperimentEvidenceClaimRepository, "get_active_for_material", lambda self, **kwargs: None)
    with Session(postgres_engine, expire_on_commit=False) as session:
        claim, created = _claim_op(ctx, key="k-1")(session)
    monkeypatch.undo()
    assert created is False and claim.id == seeded.id
    assert len(_claims(postgres_engine, ctx)) == 1 and _events(postgres_engine, ctx, CLAIMED) == 1


# --- create <-> dispose ------------------------------------------------------------------------------------------------


def test_dispose_commits_first_then_the_same_material_can_be_claimed_again(postgres_engine) -> None:
    ctx = _started_ctx(postgres_engine, "Dispose Then Create")
    seeded = _seed_claim(postgres_engine, ctx)
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _dispose_op(ctx, seeded.public_id), _claim_op(ctx, key="again")
    )
    assert isinstance(holder_result, ExperimentEvidenceClaim) and holder_result.disposed_at is not None
    assert isinstance(outcome, tuple) and outcome[1] is True and outcome[0].id != seeded.id
    rows = _claims(postgres_engine, ctx)
    assert len(rows) == 2 and len(_active(rows)) == 1  # the old one disposed, a new identity active


def test_create_commits_first_then_dispose_of_the_earlier_claim_succeeds(postgres_engine) -> None:
    ctx = _started_ctx(postgres_engine, "Create Then Dispose")
    seeded = _seed_claim(postgres_engine, ctx)
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _claim_op(ctx, key="other", entry=1), _dispose_op(ctx, seeded.public_id)
    )
    assert isinstance(holder_result, tuple) and holder_result[1] is True
    assert isinstance(outcome, ExperimentEvidenceClaim) and outcome.disposed_at is not None
    assert len(_active(_claims(postgres_engine, ctx))) == 1


@pytest.mark.parametrize("attempt", range(3))
def test_unordered_create_vs_dispose_of_the_same_material_is_coherent(postgres_engine, attempt: int) -> None:
    ctx = _started_ctx(postgres_engine, f"Create Dispose Unordered {attempt}")
    seeded = _seed_claim(postgres_engine, ctx)
    dispose_result, create_result = _race_on_experiment(
        postgres_engine, ctx, [_dispose_op(ctx, seeded.public_id), _claim_op(ctx, key="again")]
    )
    assert isinstance(dispose_result, ExperimentEvidenceClaim)  # the disposal always succeeds exactly once
    rows = _claims(postgres_engine, ctx)
    if isinstance(create_result, tuple):  # dispose won the lock: the re-claim is a new identity
        assert create_result[1] is True and len(rows) == 2
    else:  # create won the lock: the still-active duplicate is refused
        assert _code(create_result) == "EVIDENCE_CLAIM_ALREADY_ACTIVE" and len(rows) == 1
    assert len(_active(rows)) <= 1  # NEVER two active claims of one material
    assert next(row for row in rows if row.id == seeded.id).disposed_at is not None
    assert _events(postgres_engine, ctx, DISPOSED) == 1


# --- dispose <-> dispose ------------------------------------------------------------------------------------------------


def test_simultaneous_disposals_have_exactly_one_winner(postgres_engine) -> None:
    ctx = _started_ctx(postgres_engine, "Dispose Dispose")
    seeded = _seed_claim(postgres_engine, ctx)
    results = _race_on_experiment(
        postgres_engine, ctx,
        [_dispose_op(ctx, seeded.public_id, reason="First."), _dispose_op(ctx, seeded.public_id, reason="Second.")],
    )
    winners = [r for r in results if isinstance(r, ExperimentEvidenceClaim)]
    losers = [r for r in results if isinstance(r, ApiError)]
    assert len(winners) == 1 and [e.code for e in losers] == ["EVIDENCE_CLAIM_ALREADY_DISPOSED"]
    (row,) = _claims(postgres_engine, ctx)
    assert row.disposed_at is not None and row.disposal_reason in ("First.", "Second.")
    assert _events(postgres_engine, ctx, DISPOSED) == 1  # one disposal, one event, no rewrite


# --- create <-> revoke (R2, deadlock-free) --------------------------------------------------------------------------------


def test_revoke_commits_first_then_the_late_claim_succeeds(postgres_engine) -> None:
    ctx = _started_ctx(postgres_engine, "Revoke Then Claim")
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _revoke_op(ctx), _claim_op(ctx, key="late")
    )
    assert isinstance(holder_result, ExecutionAuthorization) and holder_result.revoked_at is not None
    assert isinstance(outcome, tuple) and outcome[1] is True  # R2: revocation never blocks a claim
    assert len(_claims(postgres_engine, ctx)) == 1


def test_claim_commits_first_then_revoke_succeeds_and_leaves_the_claim_untouched(postgres_engine) -> None:
    ctx = _started_ctx(postgres_engine, "Claim Then Revoke")
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _claim_op(ctx, key="c-1"), _revoke_op(ctx)
    )
    assert isinstance(holder_result, tuple) and holder_result[1] is True
    assert isinstance(outcome, ExecutionAuthorization) and outcome.revoked_at is not None
    (row,) = _claims(postgres_engine, ctx)
    assert row.disposed_at is None and row.start_id == ctx["start_id"]  # revocation never disposes or mutates a claim


@pytest.mark.parametrize("attempt", range(3))
def test_unordered_claim_vs_revoke_both_succeed_without_deadlock(postgres_engine, attempt: int) -> None:
    ctx = _started_ctx(postgres_engine, f"Claim Revoke Unordered {attempt}")
    claim_result, revoke_result = _race_on_experiment(postgres_engine, ctx, [_claim_op(ctx, key="c-1"), _revoke_op(ctx)])
    assert isinstance(claim_result, tuple) and claim_result[1] is True, claim_result
    assert isinstance(revoke_result, ExecutionAuthorization)
    assert len(_claims(postgres_engine, ctx)) == 1 and _authorizations(postgres_engine, ctx)[0].revoked_at is not None


def _is_blocked(engine, pid: int) -> bool:
    with engine.connect() as observer:
        return bool(observer.execute(text("select cardinality(pg_blocking_pids(:pid))"), {"pid": pid}).scalar_one())


def _interleave_claim_and_revoke(engine, ctx, monkeypatch, *, drop_experiment_lock: bool):
    """Forces the exact interleaving behind the lock-order rule: the claim has already INSERTED its row (so it
    holds an implicit KEY SHARE on the Authorization) and is about to write its audit event (KEY SHARE on the
    Experiment) at the moment the revoke is observed BLOCKED. Returns ``(claim_outcome, revoke_outcome)``, each a
    result or the exception it raised."""
    reached = Event()
    revoke_pid: list[int] = []
    real_record = AuditEventRepository.record

    def record(self, **kwargs):
        if kwargs.get("event_type") == CLAIMED:
            reached.set()
            deadline = monotonic() + 8
            while monotonic() < deadline and not (revoke_pid and _is_blocked(engine, revoke_pid[0])):
                sleep(0.02)
        return real_record(self, **kwargs)

    monkeypatch.setattr(AuditEventRepository, "record", record)

    def claim_worker():
        with Session(engine, expire_on_commit=False) as session:
            try:
                return _claim_op(ctx, key="interleave", drop_experiment_lock=drop_experiment_lock)(session)
            except (ApiError, DBAPIError) as exc:
                session.rollback()
                return exc

    def revoke_worker():
        with Session(engine, expire_on_commit=False) as session:
            revoke_pid.append(session.scalar(text("select pg_backend_pid()")))
            try:
                return _revoke_op(ctx)(session)
            except (ApiError, DBAPIError) as exc:
                session.rollback()
                return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim_future = pool.submit(claim_worker)
        assert reached.wait(timeout=15), "the claim never reached its audit write"
        revoke_future = pool.submit(revoke_worker)
        outcomes = (claim_future.result(timeout=30), revoke_future.result(timeout=30))
    monkeypatch.undo()
    return outcomes


def test_claim_and_revoke_interleave_deadlock_free_because_the_claim_takes_the_experiment_lock_first(
    postgres_engine, monkeypatch
) -> None:
    ctx = _started_ctx(postgres_engine, "Claim Revoke Interleave")
    claim_outcome, revoke_outcome = _interleave_claim_and_revoke(postgres_engine, ctx, monkeypatch, drop_experiment_lock=False)
    assert isinstance(claim_outcome, tuple) and claim_outcome[1] is True, claim_outcome
    assert isinstance(revoke_outcome, ExecutionAuthorization) and revoke_outcome.revoked_at is not None, revoke_outcome
    assert len(_claims(postgres_engine, ctx)) == 1 and _authorizations(postgres_engine, ctx)[0].revoked_at is not None


def test_removing_the_experiment_lock_exposes_the_deadlock_risk_mutation(postgres_engine, monkeypatch) -> None:
    """MUTATION TEST (frozen §26) — NOT a substitute for the production lock-path enumeration. With the claim
    writer's Experiment lock removed, the SAME forced interleaving deadlocks: the claim holds KEY SHARE on the
    Authorization and waits on the Experiment, while revoke holds the Experiment and waits for FOR UPDATE on the
    Authorization. PostgreSQL must detect it (SQLSTATE 40P01)."""
    ctx = _started_ctx(postgres_engine, "Claim Revoke Mutation")
    outcomes = _interleave_claim_and_revoke(postgres_engine, ctx, monkeypatch, drop_experiment_lock=True)
    deadlocks = [
        outcome for outcome in outcomes
        if isinstance(outcome, DBAPIError) and getattr(outcome.orig, "sqlstate", None) == "40P01"
    ]
    assert deadlocks, f"expected a detected deadlock when the Experiment lock is removed, got {outcomes!r}"


# --- create <-> reauthorization (both orders) --------------------------------------------------------------------------------


def _revoked_ctx(engine, name):
    ctx = _started_ctx(engine, name)
    with Session(engine, expire_on_commit=False) as session:
        _revoke_op(ctx)(session)  # a started Authorization can only be replaced after an explicit revoke (A2)
    return ctx


def test_reauthorization_commits_first_then_a_claim_on_the_old_start_still_succeeds(postgres_engine) -> None:
    ctx = _revoked_ctx(postgres_engine, "Reauth Then Claim")
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _auth_op(ctx, key="re-auth", design="Second."), _claim_op(ctx, key="c-1")
    )
    assert isinstance(holder_result, tuple) and holder_result[2] is True  # authorize B
    assert isinstance(outcome, tuple) and outcome[1] is True  # a claim names its OWN attempt (Start A)
    (row,) = _claims(postgres_engine, ctx)
    assert row.start_id == ctx["start_id"] and row.authorization_id == ctx["authorization_id"]


def test_claim_commits_first_then_reauthorization_succeeds_and_the_claim_stays_on_the_old_start(postgres_engine) -> None:
    ctx = _revoked_ctx(postgres_engine, "Claim Then Reauth")
    holder_result, outcome = _holder_then_worker_on_experiment(
        postgres_engine, ctx, _claim_op(ctx, key="c-1"), _auth_op(ctx, key="re-auth", design="Second.")
    )
    assert isinstance(holder_result, tuple) and holder_result[1] is True
    assert isinstance(outcome, tuple) and outcome[2] is True
    (row,) = _claims(postgres_engine, ctx)
    assert row.start_id == ctx["start_id"]
    assert len(_authorizations(postgres_engine, ctx)) == 2


@pytest.mark.parametrize("attempt", range(3))
def test_unordered_claim_vs_reauthorization_both_succeed(postgres_engine, attempt: int) -> None:
    ctx = _revoked_ctx(postgres_engine, f"Claim Reauth Unordered {attempt}")
    claim_result, auth_result = _race_on_experiment(
        postgres_engine, ctx, [_claim_op(ctx, key="c-1"), _auth_op(ctx, key="re-auth", design="Second.")]
    )
    assert isinstance(claim_result, tuple) and claim_result[1] is True, claim_result
    assert isinstance(auth_result, tuple) and auth_result[2] is True, auth_result
    (row,) = _claims(postgres_engine, ctx)
    assert row.start_id == ctx["start_id"]  # never reassigned to the new attempt by chronology
    rows = _authorizations(postgres_engine, ctx)
    assert len(rows) == 2 and sum(1 for r in rows if r.revoked_at is None) == 1


# --- create <-> MetricEntry correction ----------------------------------------------------------------------------------------


def test_a_metric_correction_takes_no_experiment_lock_and_never_blocks_or_retargets_a_claim(postgres_engine) -> None:
    """The correction COMPLETES while the Experiment row is held (it does not serialize with claims); the blocked
    claim then commits against its original entry. Currentness is not part of claim semantics, so no lock proves it."""
    ctx = _started_ctx(postgres_engine, "Claim Correction")
    pids: list[int] = []

    def claim_worker():
        with Session(postgres_engine, expire_on_commit=False) as session:
            pids.append(session.scalar(text("select pg_backend_pid()")))
            return _claim_op(ctx, key="c-1")(session)

    from app.strategy.models import Experiment

    with Session(postgres_engine, expire_on_commit=False) as holder, ThreadPoolExecutor(max_workers=2) as pool:
        holder.scalar(select(Experiment).where(Experiment.id == ctx["experiment_id"]).with_for_update())
        holder_pid = holder.scalar(text("select pg_backend_pid()"))
        claim_future = pool.submit(claim_worker)
        try:
            deadline = monotonic() + 15
            while not pids and monotonic() < deadline:
                sleep(0.01)
            _wait_blocked_by(postgres_engine, waiting_pids=list(pids), holding_pid=holder_pid)
            correction_future = pool.submit(lambda: _run(postgres_engine, _correction_op(ctx)))
            correction = correction_future.result(timeout=15)  # NOT blocked by the held Experiment lock
            assert not claim_future.done()  # the claim is still waiting on the Experiment row
        finally:
            holder.rollback()
        claim, created = claim_future.result(timeout=20)
    assert created is True and correction.public_id != ctx["entries"][0]
    (row,) = _claims(postgres_engine, ctx)
    assert row.metric_entry_id == ctx["entry_ids"][0]  # the claim kept its original datum


def _run(engine, operation):
    with Session(engine, expire_on_commit=False) as session:
        return operation(session)


@pytest.mark.parametrize("attempt", range(3))
def test_unordered_claim_vs_correction_both_commit_and_the_claim_never_retargets(postgres_engine, attempt: int) -> None:
    ctx = _started_ctx(postgres_engine, f"Claim Correction Unordered {attempt}")
    # NOT ``_race_on_experiment``: that harness requires every contender to be blocked on the Experiment lock, and a
    # correction deliberately takes none. The two simply run concurrently, released together.
    gate = Barrier(2)

    def gated(operation):
        def run():
            with Session(postgres_engine, expire_on_commit=False) as session:
                gate.wait(timeout=10)
                return operation(session)

        return run

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(gated(_claim_op(ctx, key="c-1"))), pool.submit(gated(_correction_op(ctx)))]
        claim_result, correction = (future.result(timeout=30) for future in futures)
    assert isinstance(claim_result, tuple) and claim_result[1] is True, claim_result
    assert correction.public_id
    (row,) = _claims(postgres_engine, ctx)
    assert row.metric_entry_id == ctx["entry_ids"][0]


# --- atomicity (real rollback, injected real constraint violation) --------------------------------------------------------------


def _fail_audit(monkeypatch, event_type: str) -> None:
    """Makes the audit insert for ``event_type`` violate a REAL FK, after the primary write was already flushed
    inside the same transaction."""
    real_record = AuditEventRepository.record

    def record(self, **kwargs):
        if kwargs.get("event_type") == event_type:
            kwargs["experiment_evidence_claim_id"] = uuid.uuid4()  # nonexistent -> real FK violation
        return real_record(self, **kwargs)

    monkeypatch.setattr(AuditEventRepository, "record", record)


def test_a_failing_audit_write_rolls_back_the_whole_claim(postgres_engine, monkeypatch) -> None:
    ctx = _started_ctx(postgres_engine, "Claim Atomic")
    _fail_audit(monkeypatch, CLAIMED)
    with Session(postgres_engine) as session:
        with pytest.raises(IntegrityError):  # an unexpected IntegrityError is re-raised, never translated
            _claim_op(ctx, key="doomed")(session)
    monkeypatch.undo()
    assert _claims(postgres_engine, ctx) == []  # 0 partial claim rows
    assert _events(postgres_engine, ctx, CLAIMED) == 0  # 0 orphan audit events
    # The key was never consumed: a clean retry now succeeds.
    with Session(postgres_engine, expire_on_commit=False) as session:
        assert _claim_op(ctx, key="doomed")(session)[1] is True


def test_a_failing_audit_write_rolls_back_the_whole_disposal(postgres_engine, monkeypatch) -> None:
    ctx = _started_ctx(postgres_engine, "Dispose Atomic")
    seeded = _seed_claim(postgres_engine, ctx)
    _fail_audit(monkeypatch, DISPOSED)
    with Session(postgres_engine) as session:
        with pytest.raises(IntegrityError):
            _dispose_op(ctx, seeded.public_id)(session)
    monkeypatch.undo()
    (row,) = _claims(postgres_engine, ctx)
    assert (row.disposed_at, row.disposed_by_user_id, row.disposal_reason) == (None, None, None)  # nothing half-disposed
    assert _events(postgres_engine, ctx, DISPOSED) == 0
    with Session(postgres_engine, expire_on_commit=False) as session:  # ...and a clean retry now succeeds
        assert _dispose_op(ctx, seeded.public_id)(session).disposed_at is not None


def test_the_committed_claim_and_disposal_events_are_user_events(postgres_engine) -> None:
    ctx = _started_ctx(postgres_engine, "Claim Actor")
    seeded = _seed_claim(postgres_engine, ctx)
    with Session(postgres_engine) as session:
        _dispose_op(ctx, seeded.public_id)(session)
    with Session(postgres_engine) as check:
        rows = check.execute(
            select(AuditEvent.event_type, AuditEvent.actor_type, AuditEvent.actor_user_id).where(
                AuditEvent.experiment_id == ctx["experiment_id"], AuditEvent.event_type.in_([CLAIMED, DISPOSED])
            )
        ).all()
    assert sorted(rows) == sorted([(CLAIMED, ActorType.USER, ctx["actor"]), (DISPOSED, ActorType.USER, ctx["actor"])])
