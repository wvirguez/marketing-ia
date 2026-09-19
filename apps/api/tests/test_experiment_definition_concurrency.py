"""Forced real-PostgreSQL overlap tests for Governed Experiment Definition
writes (MVP-37, frozen MVP-37A/-37B), mirroring
``tests/test_experiment_concurrency.py``'s ``pg_blocking_pids()`` technique:
every contending operation is observed genuinely blocked by PostgreSQL on
the Strategy row lock (held open by a separate, independent session) before
the lock is released — no sleep-based ordering, no serialized test client.

Each operation runs in its own independent DB session/backend.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic, sleep

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.campaigns.repository import CampaignRepository
from app.core.api_errors import ApiError
from app.orchestration.service import StrategyRevisionService
from app.strategy.definition_service import ExperimentDefinitionService
from app.strategy.models import Experiment, ExperimentDefinitionVersion, Strategy
from tests.strategytest import build_current_experiment
from tests.test_experiment_concurrency import _second_eligible_approval
from tests.test_experiment_definition_domain import fields

pytestmark = pytest.mark.postgres


def _setup(engine, name: str, *, first_version: bool = False):
    with Session(engine) as setup:
        campaign, strategy, _hypothesis, experiment, actor = build_current_experiment(setup, campaign_name=name)
        approval = _second_eligible_approval(setup, campaign=campaign, actor_id=actor.id)
        if first_version:
            ExperimentDefinitionService(setup).write_version(
                campaign=campaign, experiment_public_id=experiment.public_id, base_version=0,
                client_request_id="seed", fields=fields(), actor_user_id=actor.id,
            )
        setup.commit()
        return {
            "campaign": campaign.public_id, "strategy_id": strategy.id, "strategy_public": strategy.public_id,
            "experiment": experiment.public_id, "experiment_id": experiment.id, "actor": actor.id,
            "approval": approval.public_id,
        }


def _wait_blocked_by(engine, *, waiting_pids: list[int], holding_pid: int) -> None:
    """Every waiter must be observed genuinely blocked by PostgreSQL, and at
    least one directly by the holder. Row-lock waiters queue behind the
    FIRST waiter's tuple lock, so later waiters legitimately report that
    earlier waiter (not the holder) as their blocker — the chain must still
    lead back to the holder."""
    deadline = monotonic() + 15
    allowed = {holding_pid, *waiting_pids}
    all_blocked = direct_holder = False
    with engine.connect() as observer:
        while monotonic() < deadline:
            blockers_by_pid = {}
            for pid in waiting_pids:
                blockers_by_pid[pid] = set(
                    observer.execute(text("select pg_blocking_pids(:pid)"), {"pid": pid}).scalar_one()
                )
                observer.commit()
            all_blocked = all(b and b <= allowed for b in blockers_by_pid.values())
            direct_holder = any(holding_pid in b for b in blockers_by_pid.values())
            if all_blocked and direct_holder:
                return
            sleep(0.01)
    raise AssertionError(
        f"Lock wait not demonstrated (all waiters blocked: {all_blocked}; direct holder block: {direct_holder})"
    )


def _race(engine, ctx, operations):
    """Runs ``operations`` (callables taking a Session) concurrently in
    independent sessions, all forced to be blocked on the Strategy row lock
    held by a separate holder session before that lock is released."""
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
        holder.scalar(select(Strategy).where(Strategy.id == ctx["strategy_id"]).with_for_update())
        holder_pid = holder.scalar(text("select pg_backend_pid()"))
        futures = [pool.submit(worker, i) for i in range(len(operations))]
        try:
            deadline = monotonic() + 15
            while len(pids) < len(operations) and monotonic() < deadline:
                sleep(0.01)
            assert len(set(pids)) == len(operations) and holder_pid not in pids
            _wait_blocked_by(engine, waiting_pids=list(pids), holding_pid=holder_pid)
        except BaseException:
            holder.rollback()  # never leave the workers blocked behind a failed assertion
            raise
        holder.commit()
        for index, future in enumerate(futures):
            results[index] = future.result(timeout=20)
    return results


def _write_op(ctx, *, key, base_version, **overrides):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentDefinitionService(session).write_version(
            campaign=campaign, experiment_public_id=ctx["experiment"], base_version=base_version,
            client_request_id=key, fields=fields(**overrides), actor_user_id=ctx["actor"],
        )

    return operation


def _counts(engine, ctx):
    with Session(engine) as check:
        versions = check.scalar(
            select(func.count()).select_from(ExperimentDefinitionVersion).where(
                ExperimentDefinitionVersion.experiment_id == ctx["experiment_id"]
            )
        )
        audits = check.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.experiment_id == ctx["experiment_id"],
                AuditEvent.event_type.like("strategy.experiment_definition.%"),
            )
        )
        return versions, audits


# --- Case A: simultaneous first declaration, same key ------------------------------


def test_case_a_simultaneous_first_declaration_same_key(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Definition Case A")
    results = _race(
        postgres_engine, ctx,
        [_write_op(ctx, key="same-key", base_version=0), _write_op(ctx, key="same-key", base_version=0)],
    )
    assert all(isinstance(r, tuple) for r in results), results
    assert sorted(created for _row, created in results) == [False, True]  # one 201, one 200 replay
    assert results[0][0].id == results[1][0].id
    assert _counts(postgres_engine, ctx) == (1, 1)


# --- Case B: simultaneous first declaration, different keys ------------------------


def test_case_b_simultaneous_first_declaration_different_keys(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Definition Case B")
    results = _race(
        postgres_engine, ctx,
        [
            _write_op(ctx, key="key-one", base_version=0, changed_factor="One"),
            _write_op(ctx, key="key-two", base_version=0, changed_factor="Two"),
        ],
    )
    winners = [r for r in results if isinstance(r, tuple)]
    losers = [r for r in results if isinstance(r, ApiError)]
    assert len(winners) == 1 and winners[0][1] is True and winners[0][0].version == 1
    assert len(losers) == 1 and losers[0].status_code == 409 and losers[0].code == "EXPERIMENT_DEFINITION_BASE_STALE"
    assert _counts(postgres_engine, ctx) == (1, 1)


# --- Case C: simultaneous revision ---------------------------------------------------


def test_case_c_simultaneous_revision_different_keys(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Definition Case C1", first_version=True)
    results = _race(
        postgres_engine, ctx,
        [
            _write_op(ctx, key="rev-one", base_version=1, changed_factor="Rev one"),
            _write_op(ctx, key="rev-two", base_version=1, changed_factor="Rev two"),
        ],
    )
    winners = [r for r in results if isinstance(r, tuple)]
    losers = [r for r in results if isinstance(r, ApiError)]
    assert len(winners) == 1 and winners[0][0].version == 2
    assert len(losers) == 1 and losers[0].code == "EXPERIMENT_DEFINITION_BASE_STALE"
    assert _counts(postgres_engine, ctx) == (2, 2)  # seed + exactly one new version, one event each


def test_case_c_simultaneous_revision_same_key_is_one_write_and_one_replay(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Definition Case C2", first_version=True)
    results = _race(
        postgres_engine, ctx,
        [_write_op(ctx, key="rev-same", base_version=1, changed_factor="Rev"),
         _write_op(ctx, key="rev-same", base_version=1, changed_factor="Rev")],
    )
    assert all(isinstance(r, tuple) for r in results), results
    assert sorted(created for _row, created in results) == [False, True]
    assert _counts(postgres_engine, ctx) == (2, 2)


def test_same_key_racing_across_two_experiments_never_writes_both(postgres_engine) -> None:
    """The workspace-scoped key can only ever back ONE committed row: the
    second operation (a different Experiment) is an idempotency conflict."""
    ctx = _setup(postgres_engine, "Definition Key Race")
    with Session(postgres_engine) as setup:
        from app.strategy.service import StrategyService
        from app.strategy.repository import HypothesisRepository

        campaign = CampaignRepository(setup).get_by_public_id(ctx["campaign"])
        hypothesis = HypothesisRepository(setup).get_by_id(
            setup.scalar(select(Experiment.hypothesis_id).where(Experiment.id == ctx["experiment_id"]))
        )
        other = StrategyService(setup).create_experiment(
            campaign=campaign, hypothesis_public_id=hypothesis.public_id, description="other", actor_user_id=ctx["actor"],
        )
        setup.commit()
        other_public = other.public_id
        other_id = other.id

    def op_other(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentDefinitionService(session).write_version(
            campaign=campaign, experiment_public_id=other_public, base_version=0,
            client_request_id="shared", fields=fields(), actor_user_id=ctx["actor"],
        )

    results = _race(postgres_engine, ctx, [_write_op(ctx, key="shared", base_version=0), op_other])
    winners = [r for r in results if isinstance(r, tuple)]
    losers = [r for r in results if isinstance(r, ApiError)]
    assert len(winners) == 1 and len(losers) == 1 and losers[0].code == "IDEMPOTENCY_KEY_CONFLICT"
    with Session(postgres_engine) as check:
        total = check.scalar(
            select(func.count()).select_from(ExperimentDefinitionVersion).where(
                ExperimentDefinitionVersion.experiment_id.in_([ctx["experiment_id"], other_id])
            )
        )
        assert total == 1


# --- Case D: Definition write racing a Strategy revision ------------------------------


def test_case_d_definition_commits_first_then_revision_succeeds_without_deadlock(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Definition Case D1")
    pids: list[int] = []

    def revise(session):
        pids.append(session.scalar(text("select pg_backend_pid()")))
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        try:
            StrategyRevisionService(session).revise_strategy(
                campaign=campaign, base_strategy_public_id=ctx["strategy_public"],
                strategic_approval_public_id=ctx["approval"], summary="Concurrent revision.",
                positioning_statement="x", actor_user_id=ctx["actor"],
            )
            return "ok"
        except ApiError as exc:
            session.rollback()
            return exc

    with Session(postgres_engine, expire_on_commit=False) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.scalar(select(Strategy).where(Strategy.id == ctx["strategy_id"]).with_for_update())
        holder_pid = holder.scalar(text("select pg_backend_pid()"))

        def worker():
            with Session(postgres_engine, expire_on_commit=False) as session:
                return revise(session)

        future = pool.submit(worker)
        deadline = monotonic() + 15
        while not pids and monotonic() < deadline:
            sleep(0.01)
        _wait_blocked_by(postgres_engine, waiting_pids=list(pids), holding_pid=holder_pid)

        campaign = CampaignRepository(holder).get_by_public_id(ctx["campaign"])
        row, created = ExperimentDefinitionService(holder).write_version(
            campaign=campaign, experiment_public_id=ctx["experiment"], base_version=0,
            client_request_id="d1", fields=fields(), actor_user_id=ctx["actor"],
        )
        assert created is True
        outcome = future.result(timeout=20)

    assert outcome == "ok"  # the revision proceeds once the definition write commits and releases
    assert _counts(postgres_engine, ctx) == (1, 1)


def test_case_d_revision_commits_first_then_definition_write_is_rejected(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Definition Case D2")
    pids: list[int] = []

    def definition_write(session):
        pids.append(session.scalar(text("select pg_backend_pid()")))
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        try:
            return ExperimentDefinitionService(session).write_version(
                campaign=campaign, experiment_public_id=ctx["experiment"], base_version=0,
                client_request_id="d2", fields=fields(), actor_user_id=ctx["actor"],
            )
        except ApiError as exc:
            session.rollback()
            return exc

    with Session(postgres_engine, expire_on_commit=False) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.scalar(select(Strategy).where(Strategy.id == ctx["strategy_id"]).with_for_update())
        holder_pid = holder.scalar(text("select pg_backend_pid()"))

        def worker():
            with Session(postgres_engine, expire_on_commit=False) as session:
                return definition_write(session)

        future = pool.submit(worker)
        deadline = monotonic() + 15
        while not pids and monotonic() < deadline:
            sleep(0.01)
        _wait_blocked_by(postgres_engine, waiting_pids=list(pids), holding_pid=holder_pid)

        campaign = CampaignRepository(holder).get_by_public_id(ctx["campaign"])
        StrategyRevisionService(holder).revise_strategy(
            campaign=campaign, base_strategy_public_id=ctx["strategy_public"],
            strategic_approval_public_id=ctx["approval"], summary="Wins the lock.",
            positioning_statement="x", actor_user_id=ctx["actor"],
        )
        holder.commit()
        outcome = future.result(timeout=20)

    assert isinstance(outcome, ApiError)
    assert outcome.status_code == 409 and outcome.code == "EXPERIMENT_DEFINITION_STRATEGY_STALE"
    assert _counts(postgres_engine, ctx) == (0, 0)  # no write accepted against an already-superseded Strategy


def test_a_write_for_the_same_experiment_genuinely_blocks_on_the_experiment_row_lock(postgres_engine) -> None:
    """The second lock in the canonical order is real: holding ONLY the
    Experiment row (not the Strategy row) also blocks a definition write."""
    ctx = _setup(postgres_engine, "Definition Experiment Lock")
    pids: list[int] = []

    def operation():
        with Session(postgres_engine, expire_on_commit=False) as session:
            pids.append(session.scalar(text("select pg_backend_pid()")))
            campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
            return ExperimentDefinitionService(session).write_version(
                campaign=campaign, experiment_public_id=ctx["experiment"], base_version=0,
                client_request_id="explock", fields=fields(), actor_user_id=ctx["actor"],
            )

    with Session(postgres_engine, expire_on_commit=False) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.scalar(select(Experiment).where(Experiment.id == ctx["experiment_id"]).with_for_update())
        holder_pid = holder.scalar(text("select pg_backend_pid()"))
        future = pool.submit(operation)
        deadline = monotonic() + 15
        while not pids and monotonic() < deadline:
            sleep(0.01)
        _wait_blocked_by(postgres_engine, waiting_pids=list(pids), holding_pid=holder_pid)
        holder.commit()
        row, created = future.result(timeout=20)
    assert created is True and row.version == 1
