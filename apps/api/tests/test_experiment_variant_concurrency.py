"""Forced real-PostgreSQL overlap tests for Governed Variant Identity writes
(MVP-38, frozen MVP-38A/-38B), using the same ``pg_blocking_pids()``
technique as ``tests/test_experiment_definition_concurrency.py``: every
contending operation is observed genuinely blocked by PostgreSQL on the
Strategy row lock held open by a separate, independent session before that
lock is released — no sleep-based ordering, no serialized test client.

Each operation runs in its own independent DB session/backend.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.campaigns.repository import CampaignRepository
from app.core.api_errors import ApiError
from app.orchestration.service import StrategyRevisionService
from app.strategy.definition_service import ExperimentDefinitionService
from app.strategy.models import Experiment, ExperimentDefinitionVersion, ExperimentVariant, Strategy
from app.strategy.service import StrategyService
from app.strategy.variant_service import ExperimentVariantService
from tests.strategytest import build_current_experiment
from tests.test_experiment_concurrency import _second_eligible_approval
from tests.test_experiment_definition_concurrency import _race, _wait_blocked_by
from tests.test_experiment_definition_domain import fields

pytestmark = pytest.mark.postgres


def _setup(engine, name: str, *, second_experiment: bool = False):
    with Session(engine) as setup:
        campaign, strategy, hypothesis, experiment, actor = build_current_experiment(setup, campaign_name=name)
        approval = _second_eligible_approval(setup, campaign=campaign, actor_id=actor.id)
        service = ExperimentDefinitionService(setup)
        version, _created = service.write_version(
            campaign=campaign, experiment_public_id=experiment.public_id, base_version=0,
            client_request_id="seed-1", fields=fields(), actor_user_id=actor.id,
        )
        ctx = {
            "campaign": campaign.public_id, "strategy_id": strategy.id, "strategy_public": strategy.public_id,
            "experiment": experiment.public_id, "experiment_id": experiment.id, "actor": actor.id,
            "approval": approval.public_id, "version": version.public_id, "version_id": version.id,
        }
        if second_experiment:
            other = StrategyService(setup).create_experiment(
                campaign=campaign, hypothesis_public_id=hypothesis.public_id, description="second", actor_user_id=actor.id,
            )
            other_version, _c = service.write_version(
                campaign=campaign, experiment_public_id=other.public_id, base_version=0,
                client_request_id="seed-2", fields=fields(), actor_user_id=actor.id,
            )
            ctx.update(other_experiment=other.public_id, other_experiment_id=other.id, other_version=other_version.public_id)
        setup.commit()
        return ctx


def _variant_op(ctx, *, key, label, experiment=None, version=None):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentVariantService(session).declare_variant(
            campaign=campaign, experiment_public_id=experiment or ctx["experiment"],
            definition_version_public_id=version or ctx["version"], label=label, condition_description="d",
            client_request_id=key, actor_user_id=ctx["actor"],
        )

    return operation


def _revision_op(ctx, *, key="rev-1", factor="Revised factor"):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentDefinitionService(session).write_version(
            campaign=campaign, experiment_public_id=ctx["experiment"], base_version=1, client_request_id=key,
            fields=fields(changed_factor=factor), actor_user_id=ctx["actor"],
        )

    return operation


def _strategy_revision_op(ctx):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        StrategyRevisionService(session).revise_strategy(
            campaign=campaign, base_strategy_public_id=ctx["strategy_public"],
            strategic_approval_public_id=ctx["approval"], summary="Concurrent revision.",
            positioning_statement="x", actor_user_id=ctx["actor"],
        )
        return "ok"

    return operation


def _state(engine, ctx, *, experiment_ids=None):
    ids = experiment_ids or [ctx["experiment_id"]]
    with Session(engine) as check:
        variants = list(
            check.execute(
                select(ExperimentVariant.ordinal, ExperimentVariant.label).where(ExperimentVariant.experiment_id.in_(ids))
                .order_by(ExperimentVariant.ordinal)
            ).all()
        )
        versions = check.scalar(
            select(func.count()).select_from(ExperimentDefinitionVersion).where(ExperimentDefinitionVersion.experiment_id.in_(ids))
        )
        audits = check.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.experiment_id.in_(ids), AuditEvent.event_type == "strategy.variant.declared"
            )
        )
        return {"variants": variants, "versions": versions, "audits": audits}


def _holder_then_worker(engine, ctx, holder_op, worker_op):
    """The holder takes the Strategy row lock; the worker is observed
    genuinely BLOCKED by PostgreSQL on it; only then does the holder run its
    own operation (inside the lock it already holds) and commit, releasing
    the worker. Deterministic ordering — the holder's operation always wins."""
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
        holder.scalar(select(Strategy).where(Strategy.id == ctx["strategy_id"]).with_for_update())
        holder_pid = holder.scalar(text("select pg_backend_pid()"))
        future = pool.submit(worker)
        try:
            deadline = monotonic() + 15
            while not pids and monotonic() < deadline:
                sleep(0.01)
            assert pids, "worker never started"
            _wait_blocked_by(engine, waiting_pids=list(pids), holding_pid=holder_pid)
            holder_result = holder_op(holder)
        finally:
            holder.rollback()  # releases the lock even if the holder's own operation raised
        outcome = future.result(timeout=20)
    return holder_result, outcome


# --- same request ---------------------------------------------------------------------------


def test_simultaneous_same_request_same_key_is_one_write_and_one_replay(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Variant Same Request")
    results = _race(
        postgres_engine, ctx,
        [_variant_op(ctx, key="same-key", label="Variant A"), _variant_op(ctx, key="same-key", label="Variant A")],
    )
    assert all(isinstance(r, tuple) for r in results), results
    assert sorted(created for _row, _version, created in results) == [False, True]  # one 201, one 200
    assert results[0][0].id == results[1][0].id
    state = _state(postgres_engine, ctx)
    assert state["variants"] == [(1, "Variant A")] and state["audits"] == 1


# --- normalized duplicate label ---------------------------------------------------------------


def test_simultaneous_normalized_duplicate_labels_create_one_and_conflict_one(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Variant Normalized Label")
    results = _race(
        postgres_engine, ctx,
        [_variant_op(ctx, key="key-one", label="Variant A"), _variant_op(ctx, key="key-two", label="  variant   a  ")],
    )
    winners = [r for r in results if isinstance(r, tuple)]
    losers = [r for r in results if isinstance(r, ApiError)]
    assert len(winners) == 1 and winners[0][2] is True
    assert len(losers) == 1 and losers[0].status_code == 409 and losers[0].code == "EXPERIMENT_VARIANT_LABEL_DUPLICATE"
    state = _state(postgres_engine, ctx)
    assert len(state["variants"]) == 1 and state["audits"] == 1


# --- different labels ---------------------------------------------------------------------------


def test_simultaneous_different_labels_both_succeed_with_distinct_sequential_ordinals(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Variant Different Labels")
    results = _race(
        postgres_engine, ctx,
        [_variant_op(ctx, key="key-one", label="Alpha"), _variant_op(ctx, key="key-two", label="Beta")],
    )
    assert all(isinstance(r, tuple) and r[2] is True for r in results), results
    state = _state(postgres_engine, ctx)
    assert [ordinal for ordinal, _label in state["variants"]] == [1, 2]  # deterministic sequence, no gap, no duplicate
    assert {label for _ordinal, label in state["variants"]} == {"Alpha", "Beta"}
    assert state["audits"] == 2


# --- Variant vs Definition revision -----------------------------------------------------------


def test_variant_commits_first_then_the_definition_revision_is_pinned(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Variant Wins")
    holder_result, outcome = _holder_then_worker(
        postgres_engine, ctx, _variant_op(ctx, key="v-1", label="Winner"), _revision_op(ctx)
    )
    assert isinstance(holder_result, tuple) and holder_result[2] is True
    assert isinstance(outcome, ApiError) and outcome.status_code == 409 and outcome.code == "EXPERIMENT_DEFINITION_PINNED"
    state = _state(postgres_engine, ctx)
    assert state["versions"] == 1 and len(state["variants"]) == 1  # Variant(N) with NO Definition(N+1)


def test_definition_revision_commits_first_then_the_variant_is_not_current(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Revision Wins")
    holder_result, outcome = _holder_then_worker(
        postgres_engine, ctx, _revision_op(ctx), _variant_op(ctx, key="v-1", label="Late")
    )
    assert isinstance(holder_result, tuple) and holder_result[1] is True  # the revision was created
    assert isinstance(outcome, ApiError) and outcome.code == "EXPERIMENT_VARIANT_DEFINITION_VERSION_NOT_CURRENT"
    state = _state(postgres_engine, ctx)
    assert state["versions"] == 2 and state["variants"] == []  # Definition(N+1) with NO Variant(N)


@pytest.mark.parametrize("attempt", range(4))
def test_unordered_variant_vs_revision_race_never_commits_both(postgres_engine, attempt: int) -> None:
    """Both contenders start genuinely blocked on the same lock and are
    released together, so the winner is decided by PostgreSQL, not by the
    test. Whichever wins, the two must never BOTH commit from the same tip."""
    ctx = _setup(postgres_engine, f"Unordered Race {attempt}")
    variant, revision = _race(
        postgres_engine, ctx, [_variant_op(ctx, key="race-v", label="Racer"), _revision_op(ctx, key="race-r")]
    )
    state = _state(postgres_engine, ctx)
    if isinstance(variant, tuple):
        assert isinstance(revision, ApiError) and revision.code == "EXPERIMENT_DEFINITION_PINNED"
        assert (state["versions"], len(state["variants"])) == (1, 1)
    else:
        assert isinstance(revision, tuple) and isinstance(variant, ApiError)
        assert variant.code == "EXPERIMENT_VARIANT_DEFINITION_VERSION_NOT_CURRENT"
        assert (state["versions"], len(state["variants"])) == (2, 0)
    assert not (state["versions"] == 2 and len(state["variants"]) == 1)


# --- Variant vs Strategy revision ----------------------------------------------------------------


def test_variant_commits_first_then_the_strategy_revision_succeeds_without_deadlock(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Variant Then Strategy")
    holder_result, outcome = _holder_then_worker(
        postgres_engine, ctx, _variant_op(ctx, key="v-1", label="Committed"), _strategy_revision_op(ctx)
    )
    assert isinstance(holder_result, tuple) and holder_result[2] is True
    assert outcome == "ok"  # the revision proceeds once the Variant commits and releases the lock
    assert len(_state(postgres_engine, ctx)["variants"]) == 1


def test_strategy_revision_commits_first_then_the_variant_is_stale(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Strategy Then Variant")
    holder_result, outcome = _holder_then_worker(
        postgres_engine, ctx, _strategy_revision_op(ctx), _variant_op(ctx, key="v-1", label="Too late")
    )
    assert holder_result == "ok"
    assert isinstance(outcome, ApiError) and outcome.status_code == 409 and outcome.code == "EXPERIMENT_VARIANT_STRATEGY_STALE"
    state = _state(postgres_engine, ctx)
    assert state["variants"] == [] and state["audits"] == 0  # no Variant committed against a superseded Strategy


# --- cross-Experiment key ---------------------------------------------------------------------------


def test_the_same_key_racing_across_two_experiments_never_writes_both(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Variant Key Race", second_experiment=True)
    results = _race(
        postgres_engine, ctx,
        [
            _variant_op(ctx, key="shared", label="One"),
            _variant_op(ctx, key="shared", label="One", experiment=ctx["other_experiment"], version=ctx["other_version"]),
        ],
    )
    winners = [r for r in results if isinstance(r, tuple)]
    losers = [r for r in results if isinstance(r, ApiError)]
    assert len(winners) == 1 and len(losers) == 1 and losers[0].code == "IDEMPOTENCY_KEY_CONFLICT"
    state = _state(postgres_engine, ctx, experiment_ids=[ctx["experiment_id"], ctx["other_experiment_id"]])
    assert len(state["variants"]) == 1 and state["audits"] == 1  # one row, one event for the key


# --- Experiment row lock is real ---------------------------------------------------------------------


def test_a_variant_write_genuinely_blocks_on_the_experiment_row_lock(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Variant Experiment Lock")
    pids: list[int] = []

    def operation():
        with Session(postgres_engine, expire_on_commit=False) as session:
            pids.append(session.scalar(text("select pg_backend_pid()")))
            return _variant_op(ctx, key="explock", label="Blocked")(session)

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
        row, _version, created = future.result(timeout=20)
    assert created is True and row.ordinal == 1
