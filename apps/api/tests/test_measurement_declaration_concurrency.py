"""Forced real-PostgreSQL overlap and real-rollback tests for the Pre-Execution
Measurement Declaration, using the same ``pg_blocking_pids()`` technique as the
Contract / Authorization / Start / Evidence Claim suites: every contending operation
is observed genuinely blocked by PostgreSQL on a row lock held open by a separate,
independent session before that lock is released — no sleep-based ordering, no
serialized test client. Each operation runs in its own independent DB session/backend.

This capability adds NO lock participant: a structured declaration is written by the
existing Contract writer (Strategy row, then Experiment row), and the claim
compatibility check reads only immutable rows. So the assertions here are about the
EXISTING participants interacting with a STRUCTURED Contract — Contract declaration /
revision, Authorization, Start, Evidence Claim (and revoke) — never producing a
deadlock (SQLSTATE 40P01), a torn aggregate, or an inconsistent pin. A deadlock would
surface as a non-``ApiError`` database error out of a worker and fail these tests.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.repository import AuditEventRepository
from app.campaigns.repository import CampaignRepository
from app.core.api_errors import ApiError
from app.strategy.definition_service import ExperimentDefinitionService
from app.strategy.execution_authorization_service import ExperimentExecutionAuthorizationService
from app.strategy.experiment_evidence_claim_service import ExperimentEvidenceClaimService
from app.strategy.measurement_contract_service import ExperimentMeasurementContractService
from app.strategy.models import (
    ExecutionAuthorization,
    ExecutionStartAttestation,
    ExperimentEvidenceClaim,
    MeasurementContractRequiredSignal,
    MeasurementContractVersion,
)
from app.strategy.repository import MeasurementContractRepository
from app.strategy.variant_service import ExperimentVariantService
from tests.declarationtest import bound_signal, comparative_signals, descriptive_signals
from tests.evidenceclaimtest import make_entry
from tests.strategytest import build_current_experiment
from tests.test_execution_authorization_concurrency import _auth_op, _revoke_op
from tests.test_execution_start_concurrency import _race_on_experiment, _start_op
from tests.test_experiment_definition_domain import fields

pytestmark = pytest.mark.postgres

_LEGACY_FIELDS = {
    "minimum_evidence": None, "success_criterion": None, "analysis_method_intent": None, "stopping_rule": None,
    "decision_rule_intent": None,
}


def _ctx(engine, name: str, *, structured_contract: bool = True, authorize: bool = False, start: bool = False,
         entries: int = 0) -> dict:
    """Committed state: Definition v1 + one Variant [+ structured Contract v1] [+ active Authorization]
    [+ its Start] [+ N MetricEntries in the same campaign]."""
    with Session(engine, expire_on_commit=False) as setup:
        campaign, strategy, _hypothesis, experiment, actor = build_current_experiment(setup, campaign_name=name)
        version, _created = ExperimentDefinitionService(setup).write_version(
            campaign=campaign, experiment_public_id=experiment.public_id, base_version=0,
            client_request_id="seed-d", fields=fields(), actor_user_id=actor.id,
        )
        ExperimentVariantService(setup).declare_variant(
            campaign=campaign, experiment_public_id=experiment.public_id, definition_version_public_id=version.public_id,
            label="Seed", condition_description="d", client_request_id="seed-v", actor_user_id=actor.id,
        )
        ctx = {
            "campaign": campaign.public_id, "strategy_id": strategy.id, "experiment": experiment.public_id,
            "experiment_id": experiment.id, "actor": actor.id, "version": version.public_id, "version_id": version.id,
        }
        if structured_contract:
            contract = ExperimentMeasurementContractService(setup).declare_or_revise(
                campaign=campaign, experiment_public_id=experiment.public_id, base_version=0,
                client_request_id="seed-c", definition_version_public_id=version.public_id,
                measurement_window_days=14, declaration_level="DESCRIPTIVE", declaration_semantics_version=1,
                baseline_window_days=None, signals=descriptive_signals(), actor_user_id=actor.id, **_LEGACY_FIELDS,
            )[0]
            ctx["contract_id"] = contract.id
        if entries:
            ctx["entries"] = []
            for _ in range(entries):
                entry = make_entry(setup, campaign, values={"clicks": Decimal("5"), "reach": Decimal("9")}, channel="email")
                ctx["entries"].append(entry.public_id)
        setup.commit()
    if authorize or start:
        with Session(engine, expire_on_commit=False) as session:
            authorization, _snapshot, _created = _auth_op(ctx, key="seed-auth")(session)
            ctx["authorization"], ctx["authorization_id"] = authorization.public_id, authorization.id
            ctx["started_at"] = authorization.created_at + timedelta(seconds=1)
    if start:
        with Session(engine, expire_on_commit=False) as session:
            _a, started, _c = _start_op(ctx, key="seed-start")(session)
            ctx["start"], ctx["start_id"] = started.public_id, started.id
            signals = MeasurementContractRepository(session).list_signals_for_version(contract_version_id=ctx["contract_id"])
            ctx["signal"], ctx["signal_id"] = signals[0].public_id, signals[0].id
    return ctx


def _declare_op(ctx, *, key, base_version=0, level="DESCRIPTIVE", baseline=None, signals=None):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentMeasurementContractService(session).declare_or_revise(
            campaign=campaign, experiment_public_id=ctx["experiment"], base_version=base_version,
            client_request_id=key, definition_version_public_id=ctx["version"], measurement_window_days=14,
            declaration_level=level, declaration_semantics_version=1, baseline_window_days=baseline,
            signals=signals if signals is not None else (descriptive_signals() if level == "DESCRIPTIVE" else comparative_signals()),
            actor_user_id=ctx["actor"], **_LEGACY_FIELDS,
        )

    return operation


def _claim_op(ctx, *, key, entry=0, metric_name="clicks"):
    def operation(session):
        campaign = CampaignRepository(session).get_by_public_id(ctx["campaign"])
        return ExperimentEvidenceClaimService(session).create(
            campaign=campaign, experiment_public_id=ctx["experiment"], start_public_id=ctx["start"],
            client_request_id=key, required_signal_public_id=ctx["signal"], metric_entry_public_id=ctx["entries"][entry],
            metric_name=metric_name, actor_user_id=ctx["actor"],
        )

    return operation


def _code(result) -> str | None:
    return result.code if isinstance(result, ApiError) else None


def _contracts(engine, ctx) -> list[MeasurementContractVersion]:
    with Session(engine) as check:
        rows = check.execute(
            select(MeasurementContractVersion)
            .where(MeasurementContractVersion.experiment_id == ctx["experiment_id"])
            .order_by(MeasurementContractVersion.version)
        ).scalars().all()
        check.expunge_all()
        return list(rows)


def _authorizations(engine, ctx) -> list[ExecutionAuthorization]:
    with Session(engine) as check:
        rows = check.execute(
            select(ExecutionAuthorization).where(ExecutionAuthorization.experiment_id == ctx["experiment_id"])
        ).scalars().all()
        check.expunge_all()
        return list(rows)


def _count(engine, model, *where) -> int:
    with Session(engine) as check:
        return check.scalar(select(func.count()).select_from(model).where(*where))


def _no_deadlock(results) -> None:
    """Every outcome is either a value or a typed domain rejection — never a database error."""
    for result in results:
        assert not isinstance(result, BaseException) or isinstance(result, ApiError), repr(result)


# --- two structured declarations racing ------------------------------------------------------------------------------


def test_two_concurrent_structured_declarations_produce_exactly_one_version(postgres_engine) -> None:
    ctx = _ctx(postgres_engine, "Declaration Race Declare", structured_contract=False)
    results = _race_on_experiment(
        postgres_engine, ctx,
        [_declare_op(ctx, key="race-a"), _declare_op(ctx, key="race-b", level="COMPARATIVE", baseline=7)],
    )
    _no_deadlock(results)
    created = [r for r in results if isinstance(r, tuple)]
    refused = [r for r in results if isinstance(r, ApiError)]
    assert len(created) == 1 and len(refused) == 1
    assert _code(refused[0]) == "MEASUREMENT_CONTRACT_BASE_STALE"
    rows = _contracts(postgres_engine, ctx)
    assert [row.version for row in rows] == [1] and rows[0].declaration_level in {"DESCRIPTIVE", "COMPARATIVE"}
    with Session(postgres_engine) as check:
        signals = check.scalars(
            select(MeasurementContractRequiredSignal).where(MeasurementContractRequiredSignal.contract_version_id == rows[0].id)
        ).all()
    assert len(signals) == 1 and signals[0].bound_metric_name == "clicks"  # one complete aggregate, never a mix
    assert _count(
        postgres_engine, AuditEvent, AuditEvent.experiment_id == ctx["experiment_id"],
        AuditEvent.event_type == "strategy.measurement_contract.declared",
    ) == 1


def test_a_same_key_same_semantics_race_is_one_creation_and_one_replay(postgres_engine) -> None:
    ctx = _ctx(postgres_engine, "Declaration Race Replay", structured_contract=False)
    results = _race_on_experiment(postgres_engine, ctx, [_declare_op(ctx, key="same"), _declare_op(ctx, key="same")])
    _no_deadlock(results)
    assert all(isinstance(r, tuple) for r in results)
    assert sorted(r[3] for r in results) == [False, True]  # exactly one created, one replayed
    assert len(_contracts(postgres_engine, ctx)) == 1


# --- revision vs Authorization -----------------------------------------------------------------------------------------


def test_a_structured_revision_and_an_authorization_serialize_with_a_consistent_pin(postgres_engine) -> None:
    ctx = _ctx(postgres_engine, "Declaration Race Authorize")
    results = _race_on_experiment(
        postgres_engine, ctx,
        [_declare_op(ctx, key="rev", base_version=1, level="COMPARATIVE", baseline=7), _auth_op(ctx, key="race-auth")],
    )
    _no_deadlock(results)
    revision_result, authorization_result = results
    rows = _contracts(postgres_engine, ctx)
    authorizations = _authorizations(postgres_engine, ctx)
    assert len(authorizations) == 1 or isinstance(authorization_result, ApiError)
    pinned = authorizations[0].contract_version_id
    if isinstance(revision_result, tuple):  # the revision won: the Authorization pinned the NEW structured tip
        assert [row.version for row in rows] == [1, 2] and pinned == rows[1].id
        assert rows[1].declaration_level == "COMPARATIVE"
    else:  # the Authorization won: the revision was refused by the inherited C1 freeze and v1 stays the tip
        assert _code(revision_result) == "MEASUREMENT_CONTRACT_FROZEN_BY_AUTHORIZATION"
        assert [row.version for row in rows] == [1] and pinned == rows[0].id


# --- revision vs Start -----------------------------------------------------------------------------------------------------


def test_a_structured_revision_racing_a_start_can_never_change_the_started_contract(postgres_engine) -> None:
    ctx = _ctx(postgres_engine, "Declaration Race Start", authorize=True)
    results = _race_on_experiment(
        postgres_engine, ctx,
        [_declare_op(ctx, key="rev", base_version=1, level="COMPARATIVE", baseline=7), _start_op(ctx, key="race-start")],
    )
    _no_deadlock(results)
    revision_result = results[0]
    assert isinstance(revision_result, ApiError)
    assert _code(revision_result) in {
        "MEASUREMENT_CONTRACT_FROZEN_BY_AUTHORIZATION", "MEASUREMENT_CONTRACT_FROZEN_BY_EXECUTION_START",
    }
    rows = _contracts(postgres_engine, ctx)
    assert [row.version for row in rows] == [1] and rows[0].declaration_level == "DESCRIPTIVE"
    assert _count(
        postgres_engine, ExecutionStartAttestation, ExecutionStartAttestation.authorization_id == ctx["authorization_id"]
    ) == 1


def test_after_a_start_a_structured_revision_is_permanently_frozen_even_while_a_claim_lands(postgres_engine) -> None:
    ctx = _ctx(postgres_engine, "Declaration Race Claim Freeze", start=True, entries=1)
    results = _race_on_experiment(
        postgres_engine, ctx,
        [_declare_op(ctx, key="rev", base_version=1, level="COMPARATIVE", baseline=7), _claim_op(ctx, key="claim-1")],
    )
    _no_deadlock(results)
    assert _code(results[0]) == "MEASUREMENT_CONTRACT_FROZEN_BY_EXECUTION_START"
    claim, created = results[1]
    assert created is True and claim.metric_name == "clicks"
    assert [row.version for row in _contracts(postgres_engine, ctx)] == [1]


# --- claim binding checks vs revoke -----------------------------------------------------------------------------------------


def test_bound_and_mismatched_claims_racing_a_revoke_neither_deadlock_nor_tear(postgres_engine) -> None:
    ctx = _ctx(postgres_engine, "Declaration Race Claim Revoke", start=True, entries=2)
    results = _race_on_experiment(
        postgres_engine, ctx,
        [
            _claim_op(ctx, key="good", entry=0, metric_name="clicks"),
            _claim_op(ctx, key="bad", entry=1, metric_name="reach"),  # not the declared metric
            _revoke_op(ctx, reason="Stopping."),
        ],
    )
    _no_deadlock(results)
    good, bad, revoked = results
    assert isinstance(good, tuple) and good[1] is True  # late provenance claims stay allowed after revocation (R2)
    assert isinstance(bad, ApiError) and _code(bad) == "EVIDENCE_CLAIM_METRIC_NOT_BOUND"
    assert not isinstance(revoked, ApiError)
    assert _count(postgres_engine, ExperimentEvidenceClaim, ExperimentEvidenceClaim.experiment_id == ctx["experiment_id"]) == 1
    assert all(a.revoked_at is not None for a in _authorizations(postgres_engine, ctx))


# --- atomicity: a REAL constraint violation rolls the whole declaration back -----------------------------------------------


def test_a_real_failure_after_the_writes_rolls_the_structured_declaration_back(postgres_engine, monkeypatch) -> None:
    import uuid

    from app.audit.models import ActorType

    ctx = _ctx(postgres_engine, "Declaration Atomicity", structured_contract=False)
    real_record = AuditEventRepository.record

    def failing_record(self, **kwargs):
        # A REAL database FK violation, raised after the Contract row and every signal row were flushed.
        kwargs["measurement_contract_id"] = uuid.uuid4()
        kwargs["actor_type"] = ActorType.USER
        return real_record(self, **kwargs)

    monkeypatch.setattr(AuditEventRepository, "record", failing_record)
    with Session(postgres_engine, expire_on_commit=False) as session:
        with pytest.raises(IntegrityError):
            _declare_op(ctx, key="atomic", signals=[bound_signal("A"), bound_signal("B", metric="reach")])(session)
    monkeypatch.setattr(AuditEventRepository, "record", real_record)
    assert _contracts(postgres_engine, ctx) == []  # no Contract row...
    with Session(postgres_engine) as check:  # ...no orphan signal rows, no audit event
        assert check.scalar(
            select(func.count()).select_from(MeasurementContractRequiredSignal).where(
                MeasurementContractRequiredSignal.experiment_id == ctx["experiment_id"]
            )
        ) == 0
    assert _count(postgres_engine, AuditEvent, AuditEvent.experiment_id == ctx["experiment_id"], AuditEvent.event_type.like("strategy.measurement_contract.%")) == 0
    # ...and the same request then succeeds cleanly (the key was never consumed).
    with Session(postgres_engine, expire_on_commit=False) as session:
        _row, _signals, _definition, created = _declare_op(ctx, key="atomic", signals=[bound_signal("A"), bound_signal("B", metric="reach")])(session)
    assert created is True


def test_concurrent_declarations_of_different_experiments_do_not_interfere(postgres_engine) -> None:
    first = _ctx(postgres_engine, "Declaration Independent One", structured_contract=False)
    second = _ctx(postgres_engine, "Declaration Independent Two", structured_contract=False)
    barrier = Barrier(2)

    def run(ctx):
        with Session(postgres_engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            return _declare_op(ctx, key="indep")(session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, first), pool.submit(run, second)]
        results = [f.result(timeout=30) for f in futures]
    assert all(r[3] is True for r in results)
    assert len(_contracts(postgres_engine, first)) == 1 and len(_contracts(postgres_engine, second)) == 1
