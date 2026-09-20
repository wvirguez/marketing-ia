"""Forced real-PostgreSQL overlap and real-rollback tests for Governed
Execution Authorization (MVP-40, frozen Design Freeze), using the same
``pg_blocking_pids()`` technique as the Definition/Variant/Contract
concurrency suites: every contending operation is observed genuinely blocked
by PostgreSQL on a row lock held open by a separate, independent session
before that lock is released — no sleep-based ordering, no serialized test
client. Each operation runs in its own independent DB session/backend. The
atomicity tests inject a REAL database constraint violation and verify the
whole aggregate rolled back in a fresh session — no mock-only proof.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic, sleep

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType, AuditEvent
from app.audit.repository import AuditEventRepository
from app.campaigns.repository import CampaignRepository
from app.core.api_errors import ApiError
from app.orchestration.service import StrategyRevisionService
from app.strategy.definition_service import ExperimentDefinitionService
from app.strategy.execution_authorization_service import ExperimentExecutionAuthorizationService
from app.strategy.measurement_contract_service import ExperimentMeasurementContractService
from app.strategy.models import (
    ExecutionAuthorization,
    ExecutionAuthorizationVariant,
    Experiment,
    Strategy,
)
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


def _setup(engine, name: str):
    """Definition v1 + one Variant + Contract v1: the minimum authorizable state."""
    with Session(engine) as setup:
        campaign, strategy, _hypothesis, experiment, actor = build_current_experiment(setup, campaign_name=name)
        approval = _second_eligible_approval(setup, campaign=campaign, actor_id=actor.id)
        version, _created = ExperimentDefinitionService(setup).write_version(
            campaign=campaign, experiment_public_id=experiment.public_id, base_version=0,
            client_request_id="seed-d", fields=fields(), actor_user_id=actor.id,
        )
        ExperimentVariantService(setup).declare_variant(
            campaign=campaign, experiment_public_id=experiment.public_id, definition_version_public_id=version.public_id,
            label="Seed", condition_description="d", client_request_id="seed-v", actor_user_id=actor.id,
        )
        contract = ExperimentMeasurementContractService(setup).declare_or_revise(
            campaign=campaign, experiment_public_id=experiment.public_id, base_version=0,
            client_request_id="seed-c", definition_version_public_id=version.public_id, signals=[_SIGNAL],
            actor_user_id=actor.id, **_CONTRACT_FIELDS,
        )[0]
        ctx = {
            "campaign": campaign.public_id, "strategy_id": strategy.id, "strategy_public": strategy.public_id,
            "experiment": experiment.public_id, "experiment_id": experiment.id, "actor": actor.id,
            "approval": approval.public_id, "version": version.public_id, "version_id": version.id,
            "contract_id": contract.id,
        }
        setup.commit()
        return ctx


def _auth_op(ctx, *, key, design="50/50 split."):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentExecutionAuthorizationService(session).authorize(
            campaign=campaign, experiment_public_id=ctx["experiment"], client_request_id=key,
            unit_of_assignment="visitor", allocation_design=design, actor_user_id=ctx["actor"],
        )

    return operation


def _revoke_op(ctx, *, reason="revoked"):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentExecutionAuthorizationService(session).revoke(
            campaign=campaign, experiment_public_id=ctx["experiment"], reason=reason, actor_user_id=ctx["actor"],
        )

    return operation


def _contract_revision_op(ctx, *, key="c-2", base_version=1):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentMeasurementContractService(session).declare_or_revise(
            campaign=campaign, experiment_public_id=ctx["experiment"], base_version=base_version,
            client_request_id=key, definition_version_public_id=ctx["version"],
            signals=[{**_SIGNAL, "name": "Revised"}], actor_user_id=ctx["actor"], **_CONTRACT_FIELDS,
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


def _rows(engine, ctx) -> list[ExecutionAuthorization]:
    with Session(engine) as check:
        rows = check.execute(
            select(ExecutionAuthorization)
            .where(ExecutionAuthorization.experiment_id == ctx["experiment_id"])
            .order_by(ExecutionAuthorization.created_at, ExecutionAuthorization.id)
        ).scalars().all()
        check.expunge_all()
        return list(rows)


def _snapshot_count(engine, ctx) -> int:
    with Session(engine) as check:
        return check.scalar(
            select(func.count()).select_from(ExecutionAuthorizationVariant).where(
                ExecutionAuthorizationVariant.experiment_id == ctx["experiment_id"]
            )
        )


def _audit_counts(engine, ctx) -> dict[str, int]:
    with Session(engine) as check:
        return {
            kind: check.scalar(
                select(func.count()).select_from(AuditEvent).where(
                    AuditEvent.experiment_id == ctx["experiment_id"],
                    AuditEvent.event_type == f"strategy.execution_authorization.{kind}",
                )
            )
            for kind in ("authorized", "revoked")
        }


def _assert_coherent_history(rows: list[ExecutionAuthorization]) -> None:
    """One coherent active tip at most, every revoked row has its disposition, no cycles,
    no cross-Experiment link, every successor pointer resolves inside the same Experiment."""
    active = [r for r in rows if r.revoked_at is None]
    assert len(active) <= 1
    ids = {r.id: r for r in rows}
    for row in rows:
        assert (row.revoked_at is None) == (row.revoked_reason is None)
        if row.superseded_by_execution_authorization_id is not None:
            successor = ids[row.superseded_by_execution_authorization_id]  # KeyError => cross-Experiment link
            assert successor.experiment_id == row.experiment_id and successor.workspace_id == row.workspace_id
            assert row.revoked_at is not None
        seen, cursor = set(), row
        while cursor.superseded_by_execution_authorization_id is not None:
            assert cursor.id not in seen  # no cycle
            seen.add(cursor.id)
            cursor = ids[cursor.superseded_by_execution_authorization_id]


def _holder_then_worker(engine, ctx, holder_op, worker_op):
    """The holder takes the Strategy row lock; the worker is observed genuinely BLOCKED by
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
            holder.rollback()
        outcome = future.result(timeout=20)
    return holder_result, outcome


def _race_on_experiment(engine, ctx, operations):
    """Like ``_race`` but the holder session holds the EXPERIMENT row lock, so operations that
    never take the Strategy lock (revoke) are also genuinely blocked before release."""
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


# --- same request ---------------------------------------------------------------------------


def test_simultaneous_same_request_same_key_is_one_write_and_one_replay(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Auth Same Request")
    results = _race(postgres_engine, ctx, [_auth_op(ctx, key="same-key"), _auth_op(ctx, key="same-key")])
    assert all(isinstance(r, tuple) for r in results), results
    assert sorted(created for _row, _snap, created in results) == [False, True]
    assert results[0][0].id == results[1][0].id
    rows = _rows(postgres_engine, ctx)
    assert len(rows) == 1 and _snapshot_count(postgres_engine, ctx) == 1
    assert _audit_counts(postgres_engine, ctx) == {"authorized": 1, "revoked": 0}


# --- different request, same Experiment ----------------------------------------------------------


def test_simultaneous_different_keys_serialize_into_one_active_tip_with_supersession(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Auth Different Keys")
    results = _race(
        postgres_engine, ctx, [_auth_op(ctx, key="key-one", design="One."), _auth_op(ctx, key="key-two", design="Two.")]
    )
    assert all(isinstance(r, tuple) and r[2] is True for r in results), results
    rows = _rows(postgres_engine, ctx)
    assert len(rows) == 2
    _assert_coherent_history(rows)
    active = [r for r in rows if r.revoked_at is None]
    revoked = [r for r in rows if r.revoked_at is not None]
    assert len(active) == 1 and len(revoked) == 1
    assert revoked[0].superseded_by_execution_authorization_id == active[0].id
    assert revoked[0].revoked_reason == "superseded by re-authorization"
    assert _audit_counts(postgres_engine, ctx) == {"authorized": 2, "revoked": 1}


@pytest.mark.parametrize("attempt", range(3))
def test_concurrent_reauthorization_of_the_same_active_tip_keeps_one_coherent_chain(postgres_engine, attempt: int) -> None:
    ctx = _setup(postgres_engine, f"Auth Reauth Race {attempt}")
    with Session(postgres_engine) as session:
        assert _auth_op(ctx, key="first")(session)[2] is True
    results = _race(
        postgres_engine, ctx, [_auth_op(ctx, key="re-b", design="B."), _auth_op(ctx, key="re-c", design="C.")]
    )
    assert all(isinstance(r, tuple) and r[2] is True for r in results), results
    rows = _rows(postgres_engine, ctx)
    assert len(rows) == 3
    _assert_coherent_history(rows)
    assert sum(1 for r in rows if r.revoked_at is None) == 1
    # A single linear chain first -> x -> y: exactly two predecessors carry a successor pointer,
    # and no two predecessors point at the same successor (no lost disposition / no fork).
    pointers = [r.superseded_by_execution_authorization_id for r in rows if r.superseded_by_execution_authorization_id]
    assert len(pointers) == 2 and len(set(pointers)) == 2
    assert _audit_counts(postgres_engine, ctx) == {"authorized": 3, "revoked": 2}
    assert _snapshot_count(postgres_engine, ctx) == 3


# --- Contract revision vs Authorization --------------------------------------------------------------


def test_authorization_commits_first_then_a_contract_revision_is_frozen(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Auth Wins Over Contract")
    holder_result, outcome = _holder_then_worker(postgres_engine, ctx, _auth_op(ctx, key="a-1"), _contract_revision_op(ctx))
    assert isinstance(holder_result, tuple) and holder_result[2] is True
    assert isinstance(outcome, ApiError) and outcome.status_code == 409
    assert outcome.code == "MEASUREMENT_CONTRACT_FROZEN_BY_AUTHORIZATION"
    rows = _rows(postgres_engine, ctx)
    assert len(rows) == 1 and rows[0].contract_version_id == ctx["contract_id"]  # pinned to Contract N, no N+1


def test_contract_revision_commits_first_then_authorization_pins_the_new_tip(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Contract Wins Over Auth")
    holder_result, outcome = _holder_then_worker(postgres_engine, ctx, _contract_revision_op(ctx), _auth_op(ctx, key="a-1"))
    assert isinstance(holder_result, tuple) and holder_result[3] is True and holder_result[0].version == 2
    assert isinstance(outcome, tuple) and outcome[2] is True
    assert outcome[0].contract_version_id == holder_result[0].id  # pinned N+1, never the stale N
    assert outcome[0].contract_version_id != ctx["contract_id"]


@pytest.mark.parametrize("attempt", range(3))
def test_unordered_contract_vs_authorization_race_has_no_split_brain(postgres_engine, attempt: int) -> None:
    ctx = _setup(postgres_engine, f"Auth Contract Unordered {attempt}")
    contract, authorization = _race(postgres_engine, ctx, [_contract_revision_op(ctx), _auth_op(ctx, key="a-1")])
    rows = _rows(postgres_engine, ctx)
    assert len(rows) == 1  # Authorization always succeeds: it pins whatever tip it reads under the lock
    assert isinstance(authorization, tuple)
    with Session(postgres_engine) as check:
        from app.strategy.models import MeasurementContractVersion

        tip = check.execute(
            select(MeasurementContractVersion)
            .where(MeasurementContractVersion.experiment_id == ctx["experiment_id"])
            .order_by(MeasurementContractVersion.version.desc()).limit(1)
        ).scalar_one()
        tip_id, tip_version = tip.id, tip.version
    if isinstance(contract, tuple):  # revision won: Authorization pinned N+1
        assert tip_version == 2 and rows[0].contract_version_id == tip_id
    else:  # Authorization won: revision refused, still pinned to N
        assert contract.code == "MEASUREMENT_CONTRACT_FROZEN_BY_AUTHORIZATION"
        assert tip_version == 1 and rows[0].contract_version_id == tip_id == ctx["contract_id"]


# --- Variant declaration vs Authorization -----------------------------------------------------------


def test_variant_commits_first_then_the_authorization_snapshot_includes_it(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Variant Then Auth")
    holder_result, outcome = _holder_then_worker(
        postgres_engine, ctx, _variant_op(ctx, key="v-2", label="Second"), _auth_op(ctx, key="a-1")
    )
    assert isinstance(holder_result, tuple) and holder_result[2] is True
    assert isinstance(outcome, tuple) and outcome[2] is True
    assert len(outcome[1]) == 2 and _snapshot_count(postgres_engine, ctx) == 2


def test_authorization_commits_first_then_the_variant_is_still_legal_but_not_in_the_snapshot(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Auth Then Variant")
    holder_result, outcome = _holder_then_worker(
        postgres_engine, ctx, _auth_op(ctx, key="a-1"), _variant_op(ctx, key="v-2", label="Late")
    )
    assert isinstance(holder_result, tuple) and holder_result[2] is True and len(holder_result[1]) == 1
    assert isinstance(outcome, tuple) and outcome[2] is True  # Variant declaration remains legal afterward
    assert _snapshot_count(postgres_engine, ctx) == 1  # the historical snapshot never gained the late Variant


@pytest.mark.parametrize("attempt", range(3))
def test_unordered_variant_vs_authorization_race_is_deterministic(postgres_engine, attempt: int) -> None:
    ctx = _setup(postgres_engine, f"Variant Auth Unordered {attempt}")
    variant, authorization = _race(postgres_engine, ctx, [_variant_op(ctx, key="v-2", label="Racer"), _auth_op(ctx, key="a-1")])
    assert isinstance(variant, tuple) and isinstance(authorization, tuple)
    # Either the Variant serialized first (snapshot has 2) or the Authorization did (snapshot has 1) —
    # never anything else, and the snapshot always equals what the winner-ordering implies.
    assert _snapshot_count(postgres_engine, ctx) in (1, 2)
    assert len(authorization[1]) == _snapshot_count(postgres_engine, ctx)


# --- Strategy revision vs Authorization -------------------------------------------------------------


def test_authorization_commits_first_then_the_strategy_revision_succeeds_without_deadlock(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Auth Then Strategy")
    holder_result, outcome = _holder_then_worker(postgres_engine, ctx, _auth_op(ctx, key="a-1"), _strategy_revision_op(ctx))
    assert isinstance(holder_result, tuple) and holder_result[2] is True
    assert outcome == "ok"
    rows = _rows(postgres_engine, ctx)
    assert len(rows) == 1 and rows[0].revoked_at is None  # never auto-invalidated by the later revision


def test_strategy_revision_commits_first_then_a_new_authorization_is_stale(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Strategy Then Auth")
    holder_result, outcome = _holder_then_worker(postgres_engine, ctx, _strategy_revision_op(ctx), _auth_op(ctx, key="a-1"))
    assert holder_result == "ok"
    assert isinstance(outcome, ApiError) and outcome.status_code == 409
    assert outcome.code == "EXECUTION_AUTHORIZATION_STRATEGY_STALE"
    assert _rows(postgres_engine, ctx) == [] and _snapshot_count(postgres_engine, ctx) == 0
    assert _audit_counts(postgres_engine, ctx) == {"authorized": 0, "revoked": 0}


# --- Authorization vs revocation -------------------------------------------------------------------


@pytest.mark.parametrize("attempt", range(4))
def test_unordered_authorization_vs_revocation_race_is_coherent(postgres_engine, attempt: int) -> None:
    ctx = _setup(postgres_engine, f"Auth Revoke Race {attempt}")
    with Session(postgres_engine) as session:
        assert _auth_op(ctx, key="first")(session)[2] is True
    authorization, revocation = _race_on_experiment(
        postgres_engine, ctx, [_auth_op(ctx, key="second", design="Second."), _revoke_op(ctx)]
    )
    assert isinstance(authorization, tuple) and authorization[2] is True
    assert isinstance(revocation, ExecutionAuthorization)  # an active row always exists for revoke to find
    rows = _rows(postgres_engine, ctx)
    assert len(rows) == 2
    _assert_coherent_history(rows)
    first, second = rows[0], rows[1]
    assert first.revoked_at is not None and second.id == authorization[0].id
    counts = _audit_counts(postgres_engine, ctx)
    assert counts["authorized"] == 2
    if revocation.id == first.id:
        # revoke serialized first: the original was manually revoked (no successor), then a fresh row
        assert first.superseded_by_execution_authorization_id is None and second.revoked_at is None
        assert counts["revoked"] == 1
    else:
        # authorize serialized first: the original was superseded, then the new row was manually revoked
        assert first.superseded_by_execution_authorization_id == second.id
        assert second.revoked_at is not None and second.superseded_by_execution_authorization_id is None
        assert counts["revoked"] == 2
    # No double revocation side effect for any single authorization.
    with Session(postgres_engine) as check:
        per_auth = check.execute(
            select(AuditEvent.execution_authorization_id, func.count())
            .where(AuditEvent.event_type == "strategy.execution_authorization.revoked",
                   AuditEvent.experiment_id == ctx["experiment_id"])
            .group_by(AuditEvent.execution_authorization_id)
        ).all()
        assert all(count == 1 for _id, count in per_auth)


def test_two_concurrent_revocations_revoke_once_and_the_loser_finds_none_active(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Double Revoke")
    with Session(postgres_engine) as session:
        assert _auth_op(ctx, key="first")(session)[2] is True
    results = _race_on_experiment(postgres_engine, ctx, [_revoke_op(ctx, reason="a"), _revoke_op(ctx, reason="b")])
    winners = [r for r in results if isinstance(r, ExecutionAuthorization)]
    losers = [r for r in results if isinstance(r, ApiError)]
    assert len(winners) == 1 and len(losers) == 1 and losers[0].code == "EXECUTION_AUTHORIZATION_NONE_ACTIVE"
    assert _audit_counts(postgres_engine, ctx)["revoked"] == 1


def test_an_authorization_write_genuinely_blocks_on_the_experiment_row_lock(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Auth Experiment Lock")
    pids: list[int] = []

    def operation():
        with Session(postgres_engine, expire_on_commit=False) as session:
            pids.append(session.scalar(text("select pg_backend_pid()")))
            return _auth_op(ctx, key="explock")(session)

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
        _row, _snap, created = future.result(timeout=20)
    assert created is True


# --- atomicity (real rollback, injected real constraint violation) -----------------------------------


def _fail_authorized_audit(monkeypatch) -> None:
    """Makes the SUCCESSOR's ``authorized`` audit insert violate a real FK, after the parent row,
    the snapshot rows (and, on supersession, the predecessor's revocation + its audit event) were
    already flushed inside the same transaction."""
    real_record = AuditEventRepository.record

    def record(self, **kwargs):
        if kwargs.get("event_type") == "strategy.execution_authorization.authorized":
            kwargs["execution_authorization_id"] = uuid.uuid4()  # nonexistent -> real FK violation on flush
        return real_record(self, **kwargs)

    monkeypatch.setattr(AuditEventRepository, "record", record)


def test_a_failing_audit_write_rolls_back_the_whole_first_authorization_aggregate(postgres_engine, monkeypatch) -> None:
    ctx = _setup(postgres_engine, "Atomic First")
    _fail_authorized_audit(monkeypatch)
    with Session(postgres_engine) as session:
        with pytest.raises(IntegrityError):
            _auth_op(ctx, key="doomed")(session)
    monkeypatch.undo()
    assert _rows(postgres_engine, ctx) == []  # 0 partial Authorization rows
    assert _snapshot_count(postgres_engine, ctx) == 0  # 0 partial snapshot rows
    assert _audit_counts(postgres_engine, ctx) == {"authorized": 0, "revoked": 0}  # 0 orphan audit events


def test_a_failing_successor_rolls_back_and_the_predecessor_remains_active(postgres_engine, monkeypatch) -> None:
    ctx = _setup(postgres_engine, "Atomic Supersession")
    with Session(postgres_engine) as session:
        first = _auth_op(ctx, key="first")(session)[0]
        first_id = first.id
    before_snapshots = _snapshot_count(postgres_engine, ctx)
    before_audits = _audit_counts(postgres_engine, ctx)
    _fail_authorized_audit(monkeypatch)
    with Session(postgres_engine) as session:
        with pytest.raises(IntegrityError):
            _auth_op(ctx, key="doomed", design="Different.")(session)
    monkeypatch.undo()
    rows = _rows(postgres_engine, ctx)
    assert len(rows) == 1 and rows[0].id == first_id
    assert rows[0].revoked_at is None and rows[0].revoked_reason is None
    assert rows[0].superseded_by_execution_authorization_id is None  # predecessor still ACTIVE, untouched
    assert _snapshot_count(postgres_engine, ctx) == before_snapshots
    assert _audit_counts(postgres_engine, ctx) == before_audits  # incl. no orphan predecessor-revoked event


def test_atomicity_actor_type_of_committed_events_is_user(postgres_engine) -> None:
    ctx = _setup(postgres_engine, "Atomic Actor")
    with Session(postgres_engine) as session:
        _auth_op(ctx, key="first")(session)
    with Session(postgres_engine) as check:
        types = check.scalars(
            select(AuditEvent.actor_type).where(
                AuditEvent.experiment_id == ctx["experiment_id"],
                AuditEvent.event_type.like("strategy.execution_authorization.%"),
            )
        ).all()
    assert types and all(t == ActorType.USER for t in types)
