"""Shared helpers for Experiment Measurement tests — real PostgreSQL
required. Builds on the exact same real-service chain ``declarationtest.py``/
``evidenceclaimtest.py`` already use (Definition -> Variant -> Contract(+PEMD)
-> Authorization -> Start -> Claim), then invokes the real
``ExperimentMeasurementService``."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.measurement.models import MetricEntry, MetricSource
from app.strategy.execution_start_service import ExperimentExecutionStartService
from app.strategy.experiment_measurement_service import ExperimentMeasurementService
from app.strategy.models import ExecutionAuthorization, ExecutionStartAttestation, MeasurementContractRequiredSignal
from tests.declarationtest import bound_signal, build_experiment_with_definition, declare, legacy_signal
from tests.evidenceclaimtest import claim as claim_evidence
from tests.evidenceclaimtest import make_entry
from tests.test_execution_authorization_domain import _authorize, _declare_variant


@dataclass
class MeasurementStarted:
    """One STARTED execution attempt, with its pinned signals, ready for
    claims and Measurement."""

    campaign: object
    experiment: object
    actor: object
    version: object
    authorization: ExecutionAuthorization
    start: ExecutionStartAttestation
    signals: list[MeasurementContractRequiredSignal]

    @property
    def signal(self) -> MeasurementContractRequiredSignal:
        return self.signals[0]


def start_at(session, campaign, experiment, actor, authorization, *, started_at: datetime, key: str = "s-1"):
    return ExperimentExecutionStartService(session).start(
        campaign=campaign,
        experiment_public_id=experiment.public_id,
        authorization_public_id=authorization.public_id,
        client_request_id=key,
        started_at=started_at,
        actor_user_id=actor.id,
    )


def build_started(
    session,
    *,
    campaign_name: str = "Measurement Campaign",
    level: str | None = "DESCRIPTIVE",
    signals: list[dict] | None = None,
    window: int = 14,
    baseline: int | None = None,
    started_at: datetime | None = None,
    semantics_version: int = 1,
    within_workspace_id=None,
) -> MeasurementStarted:
    """A STARTED attempt whose pinned Contract carries the given declaration
    (or LEGACY, if ``level=None``). ``started_at`` is fully controllable so
    temporal-boundary tests can place it exactly. ``within_workspace_id``
    builds a second Campaign/Experiment in an EXISTING workspace (e.g. to
    test same-workspace idempotency collisions across two Starts)."""
    suffix = ""
    if within_workspace_id is not None:
        from tests.strategytest import build_current_experiment

        campaign, _strategy, _hypothesis, experiment, actor = build_current_experiment(
            session, campaign_name=campaign_name, within_workspace_id=within_workspace_id
        )
        from tests.declarationtest import _define

        suffix = "-" + uuid.uuid4().hex[:8]  # avoid client_request_id collisions within one workspace
        version = _define(session, campaign, experiment, actor, key=f"d{suffix}")
    else:
        campaign, experiment, actor, version = build_experiment_with_definition(session, campaign_name=campaign_name)
    _declare_variant(session, campaign, experiment, actor, version, label="A", key=f"v{suffix}")
    if signals is None:
        signals = [bound_signal()] if level == "DESCRIPTIVE" else [bound_signal(min_points=None)] if level == "COMPARATIVE" else [legacy_signal()]
    contract = declare(
        session,
        campaign,
        experiment,
        actor,
        version,
        signals=signals,
        level=level,
        semantics_version=semantics_version if level is not None else None,
        window=window,
        baseline=(baseline if baseline is not None else 7) if level == "COMPARATIVE" else None,
        key=f"c{suffix}",
    )[0]
    authorization, _snapshot, _created = _authorize(session, campaign, experiment, actor, key=f"a{suffix}")
    assert authorization.contract_version_id == contract.id
    anchor = started_at if started_at is not None else authorization.created_at + timedelta(seconds=1)
    _a, start, _c = start_at(session, campaign, experiment, actor, authorization, started_at=anchor, key=f"s{suffix}")
    from app.strategy.repository import MeasurementContractRepository

    rows = MeasurementContractRepository(session).list_signals_for_version(contract_version_id=contract.id)
    return MeasurementStarted(campaign, experiment, actor, version, authorization, start, rows)


def add_claim(
    session,
    started: MeasurementStarted,
    *,
    period_start: date,
    period_end: date,
    channel: str = "email",
    metric_name: str = "clicks",
    value: Decimal = Decimal("10"),
    source: MetricSource = MetricSource.MANUAL,
    signal: MeasurementContractRequiredSignal | None = None,
    entry_created_at: datetime | None = None,
    key: str | None = None,
):
    """Creates one MetricEntry/MetricValue and claims it under THIS Start's
    signal (or the given one) — returns ``(claim, created, entry)``."""
    entry = make_entry(
        session,
        started.campaign,
        values={metric_name: value},
        period_start=period_start,
        period_end=period_end,
        channel=channel,
        source=source,
        created_at=entry_created_at,
        key=uuid.uuid4().hex,
    )
    claim_row, created = claim_evidence(
        session,
        started,  # duck-typed: .campaign/.experiment/.start/.signal/.actor all present
        entry=entry,
        signal=signal,
        metric_name=metric_name,
        key=key or uuid.uuid4().hex,
    )
    return claim_row, created, entry


def run_measurement(session, started: MeasurementStarted, *, key: str | None = None, actor=None):
    """Invokes the real Measurement service; returns ``(run, created)``."""
    return ExperimentMeasurementService(session).create(
        campaign=started.campaign,
        experiment_public_id=started.experiment.public_id,
        start_public_id=started.start.public_id,
        client_request_id=key or uuid.uuid4().hex,
        actor_user_id=(actor or started.actor).id,
    )
