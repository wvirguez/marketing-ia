"""Shared helpers for Experiment Evidence Binding tests — real PostgreSQL
required. Builds a STARTED execution attempt (Definition + Variant + a Contract
with N RequiredSignals + Authorization + Start) through the real production
services, plus real MetricEntry/MetricValue rows in the SAME campaign.

``db_session`` note: ``server_default=now()`` is constant within one
transaction, so tests that depend on the ordering of two MetricEntry rows set
``created_at`` explicitly (``make_entry(created_at=...)``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.measurement.models import MetricEntry, MetricSource, MetricValue
from app.measurement.repository import MetricEntryRepository, MetricValueRepository
from app.strategy.execution_authorization_service import ExperimentExecutionAuthorizationService
from app.strategy.execution_start_service import ExperimentExecutionStartService
from app.strategy.experiment_evidence_claim_service import ExperimentEvidenceClaimService
from app.strategy.measurement_contract_service import ExperimentMeasurementContractService
from app.strategy.models import ExecutionAuthorization, ExecutionStartAttestation, MeasurementContractRequiredSignal
from app.strategy.repository import MeasurementContractRepository
from tests.strategytest import build_current_experiment
from tests.test_execution_authorization_domain import _authorize, _define, _declare_variant


def _signal_payload(name: str, *, tracking_required: bool = False) -> dict:
    return {
        "name": name,
        "description": f"{name}.",
        "expected_direction": None,
        "evidence_requirement": None,
        "tracking_required": tracking_required,
    }


def declare_contract(session, campaign, experiment, actor, version, *, signals, key="c-1", base_version=0):
    return ExperimentMeasurementContractService(session).declare_or_revise(
        campaign=campaign,
        experiment_public_id=experiment.public_id,
        base_version=base_version,
        client_request_id=key,
        definition_version_public_id=version.public_id,
        signals=signals,
        actor_user_id=actor.id,
        measurement_window_days=None,
        minimum_evidence=None,
        success_criterion=None,
        analysis_method_intent=None,
        stopping_rule=None,
        decision_rule_intent=None,
    )[0]


@dataclass
class Started:
    """One STARTED execution attempt plus the campaign it lives in."""

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


def build_started(
    session,
    *,
    campaign_name: str = "Claim Campaign",
    within_workspace_id=None,
    suffix: str = "",
    signal_names: tuple[str, ...] = ("Click-through rate",),
    tracking_required: bool = False,
) -> Started:
    """A started attempt through the real Authorization and Start services."""
    campaign, _strategy, _hypothesis, experiment, actor = build_current_experiment(
        session, campaign_name=campaign_name, within_workspace_id=within_workspace_id
    )
    version = _define(session, campaign, experiment, actor, key=f"d-1{suffix}")
    _declare_variant(session, campaign, experiment, actor, version, label="A", key=f"v-1{suffix}")
    contract = declare_contract(
        session,
        campaign,
        experiment,
        actor,
        version,
        signals=[_signal_payload(name, tracking_required=tracking_required) for name in signal_names],
        key=f"c-1{suffix}",
    )
    authorization, _snapshot, _created = _authorize(session, campaign, experiment, actor, key=f"a-1{suffix}")
    assert authorization.contract_version_id == contract.id
    _a, start, _c = start_authorization(session, campaign, experiment, actor, authorization, key=f"s-1{suffix}")
    signals = MeasurementContractRepository(session).list_signals_for_version(contract_version_id=contract.id)
    return Started(campaign, experiment, actor, version, authorization, start, signals)


def start_authorization(session, campaign, experiment, actor, authorization, *, key="s-1"):
    return ExperimentExecutionStartService(session).start(
        campaign=campaign,
        experiment_public_id=experiment.public_id,
        authorization_public_id=authorization.public_id,
        client_request_id=key,
        started_at=authorization.created_at + timedelta(seconds=1),
        actor_user_id=actor.id,
    )


def make_entry(
    session,
    campaign,
    *,
    values: dict[str, Decimal] | None = None,
    period_start: date = date(2026, 1, 1),
    period_end: date = date(2026, 1, 31),
    channel: str = "email",
    source: MetricSource = MetricSource.MANUAL,
    created_at: datetime | None = None,
    key: str | None = None,
) -> MetricEntry:
    entry = MetricEntryRepository(session).create(
        campaign=campaign,
        period_start=period_start,
        period_end=period_end,
        channel=channel,
        source=source,
        client_request_id=key or uuid.uuid4().hex,
    )
    MetricValueRepository(session).create_many(
        metric_entry=entry, values=values if values is not None else {"clicks": Decimal("50"), "impressions": Decimal("1000")}
    )
    if created_at is not None:
        entry.created_at = created_at
        session.flush()
    return entry


def claim(
    session,
    started: Started,
    *,
    entry: MetricEntry,
    signal: MeasurementContractRequiredSignal | None = None,
    metric_name: str = "clicks",
    key: str | None = None,
    actor=None,
    start: ExecutionStartAttestation | None = None,
):
    """Creates a claim through the real service; returns ``(claim, created)``."""
    return ExperimentEvidenceClaimService(session).create(
        campaign=started.campaign,
        experiment_public_id=started.experiment.public_id,
        start_public_id=(start or started.start).public_id,
        client_request_id=key or uuid.uuid4().hex,
        required_signal_public_id=(signal or started.signal).public_id,
        metric_entry_public_id=entry.public_id,
        metric_name=metric_name,
        actor_user_id=(actor or started.actor).id,
    )


def dispose(session, started: Started, claim_row, *, reason="No longer intended.", actor=None):
    return ExperimentEvidenceClaimService(session).dispose(
        campaign=started.campaign,
        experiment_public_id=started.experiment.public_id,
        start_public_id=started.start.public_id,
        claim_public_id=claim_row.public_id,
        reason=reason,
        actor_user_id=(actor or started.actor).id,
    )


def revoke(session, started: Started, *, reason="Stopping."):
    return ExperimentExecutionAuthorizationService(session).revoke(
        campaign=started.campaign,
        experiment_public_id=started.experiment.public_id,
        reason=reason,
        actor_user_id=started.actor.id,
    )


def utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def build_distribution_in_campaign(session, started: Started):
    """A DISTRIBUTED ``ContentDistribution`` inside the SAME campaign as the
    started Experiment (needed to create genuine distribution-owned entries)."""
    from sqlalchemy import select

    from app.campaigns.models import CampaignRun
    from app.content.models import ContentApprovalStatus, ContentDistribution
    from app.content.service import ContentService
    from app.orchestration.models import BusinessStage, RunStageExecution
    from app.planning.service import PlanningService
    from tests.contenttest import default_piece_fields, default_plan_item, default_version_payload, make_user

    campaign = started.campaign
    run = session.execute(select(CampaignRun).where(CampaignRun.campaign_id == campaign.id)).scalars().first()
    stage = session.execute(
        select(RunStageExecution).where(
            RunStageExecution.campaign_run_id == run.id, RunStageExecution.stage == BusinessStage.PLAN
        )
    ).scalar_one()
    plan, items = PlanningService(session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stage, summary="A calendar.", items=[default_plan_item()]
    )
    user = make_user(session)
    service = ContentService(session)
    brief = service.record_brief(plan_item=items[0], content_plan=plan, brief="Claim evidence")
    piece, _version = service.record_piece(
        content_brief=brief, initial_payload=default_version_payload(), **default_piece_fields()
    )
    for transition in (service.mark_in_production, service.mark_produced, service.mark_ready_for_review):
        transition(workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id)
    approval = service.request_approval(
        workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id
    )
    service.mark_under_review(
        workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id, actor_user_id=user.id
    )
    service.record_authorized_approval_decision(
        workspace_id=campaign.workspace_id, content_approval_public_id=approval.public_id,
        decision=ContentApprovalStatus.APPROVED, actor_user_id=user.id,
    )
    service.mark_ready_for_distribution(
        workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id
    )
    service.record_distributed(
        workspace_id=campaign.workspace_id, content_piece_public_id=piece.public_id, actor_user_id=user.id
    )
    distribution = session.execute(
        select(ContentDistribution).where(ContentDistribution.content_piece_id == piece.id)
    ).scalar_one()
    return distribution, user


def make_distribution_evidence(session, started: Started, distribution, user, *, values=None, source_reference="ref"):
    """A genuine distribution-owned MetricEntry via the production service.
    Returns ``(evidence, entry)``."""
    from datetime import datetime as _dt

    from app.measurement.service import MeasurementService

    today = _dt.now(timezone.utc).date()
    evidence, _created = MeasurementService(session).create_distribution_evidence(
        distribution=distribution,
        campaign=started.campaign,
        period_start=today - timedelta(days=3),
        period_end=today,
        metric_values=values if values is not None else {"reach": Decimal("500")},
        client_request_id=uuid.uuid4().hex,
        source_reference=source_reference,
        actor_user_id=user.id,
    )
    entry = MetricEntryRepository(session).get_by_id(evidence.metric_entry_id)
    return evidence, entry


def correct_distribution_evidence(session, started: Started, distribution, user, target, *, values=None):
    from datetime import datetime as _dt

    from app.measurement.service import MeasurementService

    today = _dt.now(timezone.utc).date()
    evidence, _created = MeasurementService(session).create_distribution_evidence_correction(
        distribution=distribution,
        campaign=started.campaign,
        target_evidence_public_id=target.public_id,
        period_start=today - timedelta(days=3),
        period_end=today,
        metric_values=values if values is not None else {"reach": Decimal("600")},
        client_request_id=uuid.uuid4().hex,
        source_reference="ref-2",
        correction_reason="Corrected.",
        actor_user_id=user.id,
    )
    return evidence, MetricEntryRepository(session).get_by_id(evidence.metric_entry_id)
