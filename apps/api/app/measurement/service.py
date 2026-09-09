"""Measurement persistence — BACKEND-11.

Two different write shapes, deliberately:

1. ``record_metric_entry`` is the **only** public-write-eligible method in
   this module (§10/§11/§12 of the Governance Freeze) — ordinary
   authenticated workspace membership is sufficient authorization; this is
   not a governance-approval question the way Content Approval was.
   Idempotent by construction: a retried POST/PUT sharing the same
   ``(workspace_id, client_request_id)`` returns the original row rather
   than raising or duplicating (race-safe via the DB's own unique
   constraint, not an application-level check-then-insert).
2. ``record_observation``/``record_signal``/``record_analysis_result`` are
   service-layer-only, exactly like every derived/computed entity in every
   prior stage — no public POST/PATCH exists for any of them.

CORRECTIONS: a PUT is not a SQL UPDATE. ``record_metric_entry`` with
``is_correction=True`` still only ever *inserts* a new, independent
``MetricEntry`` row sharing the same logical
``(campaign_id, period_start, period_end, channel)`` grouping as a prior
entry — the prior row is never read, locked, or touched. "Current" is
derived at read time (`created_at DESC, id DESC` within a grouping), never
stored as a pointer.

RECORDING AN OBSERVATION/SIGNAL/ANALYSIS RESULT NEVER MUTATES ITS SOURCE
ROWS — every source-row check in this module is read-only. No method here
mutates ``CampaignRun``, ``RunStageExecution``, any HITL table, Content,
Distribution, Paid Media, or Learning.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.core.api_errors import ProvenanceMismatchError
from app.measurement.models import AnalysisResult, MetricEntry, MetricSource, MetricValue, PerformanceObservation, PerformanceSignal
from app.measurement.repository import (
    AnalysisResultRepository,
    AnalysisResultSignalRepository,
    MetricEntryRepository,
    MetricValueRepository,
    ObservationMetricEntryRepository,
    PerformanceObservationRepository,
    PerformanceSignalRepository,
    SignalObservationRepository,
)

EVENT_METRIC_ENTRY_RECORDED = "measurement.metric_entry.recorded"
EVENT_METRIC_ENTRY_CORRECTION_RECORDED = "measurement.metric_entry.correction_recorded"
EVENT_OBSERVATION_RECORDED = "measurement.observation.recorded"
EVENT_SIGNAL_RECORDED = "measurement.signal.recorded"
EVENT_ANALYSIS_RESULT_RECORDED = "measurement.analysis_result.recorded"


def _validate_same_workspace_and_campaign(*, campaign: Campaign, rows: list, row_label: str) -> None:
    """A plain FK alone cannot prove this — the same "check explicitly,
    never infer" discipline used everywhere else in this codebase for
    cross-entity consistency."""
    if not rows:
        raise ProvenanceMismatchError(f"At least one source {row_label} is required.")
    for row in rows:
        if row.workspace_id != campaign.workspace_id or row.campaign_id != campaign.id:
            raise ProvenanceMismatchError()


class MeasurementService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.entries = MetricEntryRepository(session)
        self.values = MetricValueRepository(session)
        self.observations = PerformanceObservationRepository(session)
        self.signals = PerformanceSignalRepository(session)
        self.analysis_results = AnalysisResultRepository(session)
        self.observation_entries = ObservationMetricEntryRepository(session)
        self.signal_observations = SignalObservationRepository(session)
        self.analysis_result_signals = AnalysisResultSignalRepository(session)
        self.events = AuditEventRepository(session)

    # --- reads (GET-only / GET-write-shared surface) --------------------

    def list_metric_entries_for_campaign(self, *, campaign_id: uuid.UUID) -> list[tuple[MetricEntry, list[MetricValue], bool]]:
        """Full history, every correction preserved. Returns
        ``(entry, values, is_current)`` tuples — ``is_current`` is computed
        fresh on every read (the first entry encountered per
        ``(period_start, period_end, channel)`` grouping, given the
        repository's own ordering), never a stored/mutable pointer."""
        entries = self.entries.list_for_campaign(campaign_id)
        values_by_entry_id: dict[uuid.UUID, list[MetricValue]] = {}
        if entries:
            all_values = self.values.list_for_entries([e.id for e in entries])
            for value in all_values:
                values_by_entry_id.setdefault(value.metric_entry_id, []).append(value)

        seen_groupings: set[tuple] = set()
        result: list[tuple[MetricEntry, list[MetricValue], bool]] = []
        for entry in entries:
            grouping = (entry.period_start, entry.period_end, entry.channel)
            is_current = grouping not in seen_groupings
            seen_groupings.add(grouping)
            result.append((entry, values_by_entry_id.get(entry.id, []), is_current))
        return result

    def get_analysis_for_campaign(
        self, *, campaign_id: uuid.UUID
    ) -> tuple[list[PerformanceObservation], list[PerformanceSignal], list[AnalysisResult]]:
        observations = self.observations.list_for_campaign(campaign_id)
        signals = self.signals.list_for_campaign(campaign_id)
        results = self.analysis_results.list_for_campaign(campaign_id)
        return observations, signals, results

    def get_source_metric_entry_ids(self, observation_id: uuid.UUID) -> list[uuid.UUID]:
        return self.observation_entries.list_metric_entry_ids_for_observation(observation_id)

    def get_source_observation_ids(self, signal_id: uuid.UUID) -> list[uuid.UUID]:
        return self.signal_observations.list_observation_ids_for_signal(signal_id)

    def get_source_signal_ids(self, analysis_result_id: uuid.UUID) -> list[uuid.UUID]:
        return self.analysis_result_signals.list_signal_ids_for_analysis_result(analysis_result_id)

    # --- Metric Entry: the one public-write-eligible path ---------------

    def record_metric_entry(
        self,
        *,
        campaign: Campaign,
        period_start,
        period_end,
        channel: str,
        source: MetricSource,
        client_request_id: str,
        metric_values: dict[str, Decimal],
        is_correction: bool = False,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> MetricEntry:
        """Race-safe, DB-backed idempotency: attempts the insert first and
        lets the database's own ``UNIQUE(workspace_id, client_request_id)``
        constraint be authoritative — two concurrent callers with the same
        ``client_request_id`` can both attempt this; exactly one wins the
        insert, and the other observes the ``IntegrityError``, rolls back,
        and returns the winner's already-committed row. This is a genuine
        idempotent replay, not a conflict to surface to the caller."""
        if not metric_values:
            raise ProvenanceMismatchError("At least one metric value is required.")

        try:
            entry = self.entries.create(
                campaign=campaign, period_start=period_start, period_end=period_end,
                channel=channel, source=source, client_request_id=client_request_id,
            )
        except IntegrityError:
            self.session.rollback()
            existing = self.entries.get_by_workspace_and_request_id(
                workspace_id=campaign.workspace_id, client_request_id=client_request_id
            )
            if existing is not None:
                return existing
            raise

        self.values.create_many(metric_entry=entry, values=metric_values)

        actor_type = ActorType.USER if actor_user_id is not None else ActorType.SYSTEM
        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_METRIC_ENTRY_CORRECTION_RECORDED if is_correction else EVENT_METRIC_ENTRY_RECORDED,
            actor_type=actor_type,
            campaign_id=campaign.id,
            metric_entry_id=entry.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return entry

    # --- derived entities: service-layer only ----------------------------

    def record_observation(
        self,
        *,
        campaign: Campaign,
        metric_entries: list[MetricEntry],
        metric_name: str,
        value: Decimal,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> PerformanceObservation:
        _validate_same_workspace_and_campaign(campaign=campaign, rows=metric_entries, row_label="Metric Entry")

        observation = self.observations.create(campaign=campaign, metric_name=metric_name, value=value)
        self.observation_entries.create_many(observation=observation, metric_entries=metric_entries)

        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_OBSERVATION_RECORDED,
            actor_type=ActorType.USER if actor_user_id is not None else ActorType.SYSTEM,
            campaign_id=campaign.id,
            performance_observation_id=observation.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return observation

    def record_signal(
        self,
        *,
        campaign: Campaign,
        observations: list[PerformanceObservation],
        summary: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> PerformanceSignal:
        _validate_same_workspace_and_campaign(campaign=campaign, rows=observations, row_label="Performance Observation")

        signal = self.signals.create(campaign=campaign, summary=summary)
        self.signal_observations.create_many(signal=signal, observations=observations)

        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_SIGNAL_RECORDED,
            actor_type=ActorType.USER if actor_user_id is not None else ActorType.SYSTEM,
            campaign_id=campaign.id,
            performance_signal_id=signal.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return signal

    def record_analysis_result(
        self,
        *,
        campaign: Campaign,
        signals: list[PerformanceSignal],
        summary: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> AnalysisResult:
        _validate_same_workspace_and_campaign(campaign=campaign, rows=signals, row_label="Performance Signal")

        analysis_result = self.analysis_results.create(campaign=campaign, summary=summary)
        self.analysis_result_signals.create_many(analysis_result=analysis_result, signals=signals)

        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_ANALYSIS_RESULT_RECORDED,
            actor_type=ActorType.USER if actor_user_id is not None else ActorType.SYSTEM,
            campaign_id=campaign.id,
            analysis_result_id=analysis_result.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return analysis_result
