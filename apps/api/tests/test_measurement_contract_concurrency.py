"""Forced real-PostgreSQL overlap tests for Governed Measurement Contract
writes (MVP-39, frozen MVP-39A/-39B), using the same ``pg_blocking_pids()``
technique as ``tests/test_experiment_definition_concurrency.py`` /
``tests/test_experiment_variant_concurrency.py``: every contending
operation is observed genuinely blocked by PostgreSQL on the Strategy row
lock held open by a separate, independent session before that lock is
released — no sleep-based ordering, no serialized test client.

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
from app.strategy.measurement_contract_service import ExperimentMeasurementContractService
from app.strategy.models import Experiment, ExperimentDefinitionVersion, MeasurementContractVersion, Strategy
from app.strategy.service import StrategyService
from app.strategy.variant_service import ExperimentVariantService
from tests.strategytest import build_current_experiment
from tests.test_experiment_concurrency import _second_eligible_approval
from tests.test_experiment_definition_concurrency import _race, _wait_blocked_by
from tests.test_experiment_definition_domain import fields

pytestmark = pytest.mark.postgres

_SIGNAL = {"name": "CTR", "description": "d", "expected_direction": None, "evidence_requirement": None, "tracking_required": False}
_CONTRACT_FIELDS = {
    "measurement_window_days": None, "minimum_evidence": None, "success_criterion": None,
    "analysis_method_intent": None, "stopping_rule": None, "decision_rule_intent": None,
}


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


def _contract_op(ctx, *, key, base_version=0, signal_name="CTR", experiment=None, version=None):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentMeasurementContractService(session).declare_or_revise(
            campaign=campaign, experiment_public_id=experiment or ctx["experiment"],
            base_version=base_version, client_request_id=key, definition_version_public_id=version or ctx["version"],
            signals=[{**_SIGNAL, "name": signal_name}], actor_user_id=ctx["actor"], **_CONTRACT_FIELDS,
        )

    return operation


def _variant_op(ctx, *, key, label):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentVariantService(session).declare_variant(
            campaign=campaign, experiment_public_id=ctx["experiment"], definition_version_public_id=ctx["version"],
            label=label, condition_description="d", client_request_id=key, actor_user_id=ctx["actor"],
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
        contracts = check.scalar(
            select(func.count()).select_from(MeasurementContractVersion).where(MeasurementContractVersion.experiment_id.in_(ids))
        )
        versions = check.scalar(
            select(func.count()).select_from(ExperimentDefinitionVersion).where(ExperimentDefinitionVersion.experiment_id.in_(ids))
        )
        audits = check.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.experiment_id.in_(ids), AuditEvent.event_type.like("strategy.measurement_contract.%")
            )
        )
        return {"contracts": contracts, "versions": versions, "audits": audits}


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
    ctx = _setup(postgres_engine, "Contract Same Request")
    results = _race(postgres_engine, ctx, [_contract_op(ctx, key="same-key"), _contract_op(ctx, key="same-key")])
    assert all(isinstance(r, tuple) for r in results), results
    assert sorted(created for _row, _signals, _pinned, created in results) == [False, True]
    assert results[0][0].id == results[1][0].id
    state = _state(postgres_engine, ctx)
    assert state["contracts"] == 1 and state["audits"] == 1


# --- different key, same base -----------------------------------------------------------------


def test_simultaneous_different_keys_same_base_one_wins_one_is_base_stale(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Contract Different Keys")
    results = _race(
        postgres_engine, ctx,
        [_contract_op(ctx, key="key-one", signal_name="Alpha"), _contract_op(ctx, key="key-two", signal_name="Beta")],
    )
    winners = [r for r in results if isinstance(r, tuple)]
    losers = [r for r in results if isinstance(r, ApiError)]
    assert len(winners) == 1 and winners[0][3] is True
    assert len(losers) == 1 and losers[0].code == "MEASUREMENT_CONTRACT_BASE_STALE"
    state = _state(postgres_engine, ctx)
    assert state["contracts"] == 1 and state["audits"] == 1  # no duplicate Contract version


# --- Contract vs Definition revision -----------------------------------------------------------


def test_contract_commits_first_then_the_definition_revision_is_pinned(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Contract Wins")
    holder_result, outcome = _holder_then_worker(postgres_engine, ctx, _contract_op(ctx, key="c-1"), _revision_op(ctx))
    assert isinstance(holder_result, tuple) and holder_result[3] is True
    assert isinstance(outcome, ApiError) and outcome.status_code == 409 and outcome.code == "EXPERIMENT_DEFINITION_PINNED"
    state = _state(postgres_engine, ctx)
    assert state["versions"] == 1 and state["contracts"] == 1  # Contract(N) with NO Definition(N+1)


def test_definition_revision_commits_first_then_the_contract_is_not_current(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Revision Wins")
    holder_result, outcome = _holder_then_worker(postgres_engine, ctx, _revision_op(ctx), _contract_op(ctx, key="c-1"))
    assert isinstance(holder_result, tuple) and holder_result[1] is True  # the revision was created
    assert isinstance(outcome, ApiError) and outcome.code == "MEASUREMENT_CONTRACT_DEFINITION_VERSION_NOT_CURRENT"
    state = _state(postgres_engine, ctx)
    assert state["versions"] == 2 and state["contracts"] == 0  # Definition(N+1) with NO Contract(N)


@pytest.mark.parametrize("attempt", range(4))
def test_unordered_contract_vs_revision_race_never_commits_both(postgres_engine, attempt: int) -> None:
    """Both contenders start genuinely blocked on the same lock and are
    released together, so the winner is decided by PostgreSQL, not by the
    test. Whichever wins, the two must never BOTH commit from the same tip."""
    ctx = _setup(postgres_engine, f"Contract Unordered Race {attempt}")
    contract, revision = _race(postgres_engine, ctx, [_contract_op(ctx, key="race-c"), _revision_op(ctx, key="race-r")])
    state = _state(postgres_engine, ctx)
    if isinstance(contract, tuple):
        assert isinstance(revision, ApiError) and revision.code == "EXPERIMENT_DEFINITION_PINNED"
        assert (state["versions"], state["contracts"]) == (1, 1)
    else:
        assert isinstance(revision, tuple) and isinstance(contract, ApiError)
        assert contract.code == "MEASUREMENT_CONTRACT_DEFINITION_VERSION_NOT_CURRENT"
        assert (state["versions"], state["contracts"]) == (2, 0)
    assert not (state["versions"] == 2 and state["contracts"] == 1)


# --- Contract vs Variant: independent siblings ---------------------------------------------------


def test_contract_commits_first_then_a_variant_still_succeeds(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Contract Then Variant")
    holder_result, outcome = _holder_then_worker(
        postgres_engine, ctx, _contract_op(ctx, key="c-1"), _variant_op(ctx, key="v-1", label="Still allowed")
    )
    assert isinstance(holder_result, tuple) and holder_result[3] is True
    assert isinstance(outcome, tuple) and outcome[2] is True  # the Variant is NOT blocked by the Contract's own pin


def test_variant_commits_first_then_a_contract_still_succeeds(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Variant Then Contract")
    holder_result, outcome = _holder_then_worker(
        postgres_engine, ctx, _variant_op(ctx, key="v-1", label="First"), _contract_op(ctx, key="c-1")
    )
    assert isinstance(holder_result, tuple) and holder_result[2] is True
    assert isinstance(outcome, tuple) and outcome[3] is True  # the Contract is NOT blocked by the Variant's own pin


def test_simultaneous_contract_and_variant_both_succeed(postgres_engine) -> None:
    """Unordered race between two DIFFERENT pinning-child types: since
    neither checks the other's existence, both must succeed regardless of
    which one the database happens to serialize first."""
    ctx = _setup(postgres_engine, "Contract And Variant Together")
    contract, variant = _race(
        postgres_engine, ctx, [_contract_op(ctx, key="c-1"), _variant_op(ctx, key="v-1", label="Together")]
    )
    assert isinstance(contract, tuple) and contract[3] is True
    assert isinstance(variant, tuple) and variant[2] is True


# --- Contract vs Strategy revision ----------------------------------------------------------------


def test_contract_commits_first_then_the_strategy_revision_succeeds_without_deadlock(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Contract Then Strategy")
    holder_result, outcome = _holder_then_worker(postgres_engine, ctx, _contract_op(ctx, key="c-1"), _strategy_revision_op(ctx))
    assert isinstance(holder_result, tuple) and holder_result[3] is True
    assert outcome == "ok"  # the revision proceeds once the Contract commits and releases the lock
    assert _state(postgres_engine, ctx)["contracts"] == 1


def test_strategy_revision_commits_first_then_the_contract_is_stale(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Strategy Then Contract")
    holder_result, outcome = _holder_then_worker(postgres_engine, ctx, _strategy_revision_op(ctx), _contract_op(ctx, key="c-1"))
    assert holder_result == "ok"
    assert isinstance(outcome, ApiError) and outcome.status_code == 409 and outcome.code == "MEASUREMENT_CONTRACT_STRATEGY_STALE"
    state = _state(postgres_engine, ctx)
    assert state["contracts"] == 0 and state["audits"] == 0  # no Contract committed against a superseded Strategy


# --- cross-Experiment key ---------------------------------------------------------------------------


def test_the_same_key_racing_across_two_experiments_never_writes_both(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Contract Key Race", second_experiment=True)
    results = _race(
        postgres_engine, ctx,
        [
            _contract_op(ctx, key="shared"),
            _contract_op(ctx, key="shared", experiment=ctx["other_experiment"], version=ctx["other_version"]),
        ],
    )
    winners = [r for r in results if isinstance(r, tuple)]
    losers = [r for r in results if isinstance(r, ApiError)]
    assert len(winners) == 1 and len(losers) == 1 and losers[0].code == "IDEMPOTENCY_KEY_CONFLICT"
    state = _state(postgres_engine, ctx, experiment_ids=[ctx["experiment_id"], ctx["other_experiment_id"]])
    assert state["contracts"] == 1 and state["audits"] == 1  # one row, one event for the key


# --- Experiment row lock is real ---------------------------------------------------------------------


def test_a_contract_write_genuinely_blocks_on_the_experiment_row_lock(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Contract Experiment Lock")
    pids: list[int] = []

    def operation():
        with Session(postgres_engine, expire_on_commit=False) as session:
            pids.append(session.scalar(text("select pg_backend_pid()")))
            return _contract_op(ctx, key="explock")(session)

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
        row, _signals, _pinned, created = future.result(timeout=20)
    assert created is True and row.version == 1
