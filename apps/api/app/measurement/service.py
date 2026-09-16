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
from datetime import date
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.content.models import ContentDistribution, ContentPiece
from app.core.api_errors import (
    EvidenceCorrectionTargetStaleError,
    EvidencePeriodInvalidError,
    ForbiddenError,
    IdempotencyKeyConflictError,
    ProvenanceMismatchError,
)
from app.measurement.models import (
    AnalysisResult,
    DistributionMetricEvidence,
    MetricEntry,
    MetricSource,
    MetricValue,
    PerformanceObservation,
    PerformanceSignal,
)
from app.measurement.repository import (
    AnalysisResultRepository,
    AnalysisResultSignalRepository,
    DistributionMetricEvidenceRepository,
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
EVENT_DISTRIBUTION_EVIDENCE_RECORDED = "measurement.distribution_evidence.recorded"
EVENT_DISTRIBUTION_EVIDENCE_CORRECTED = "measurement.distribution_evidence.corrected"


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
        self.evidence = DistributionMetricEvidenceRepository(session)
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

    def _record_metric_entry_uncommitted(
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
        """MVP-19B §33/§39 non-committing primitive: creates the
        MetricEntry, its MetricValues, and its audit event, ending in
        ``flush()`` only — never ``commit()``, and never itself catches the
        ``IntegrityError`` its own insert may raise on
        ``UNIQUE(workspace_id, client_request_id)``. Extracted from
        ``record_metric_entry`` (byte-for-byte unchanged external
        behavior) specifically so ``DistributionMetricEvidence`` creation
        can call this inside its *own* ``session.begin_nested()``
        savepoint, atomically with the Evidence row — the published
        ``record_metric_entry`` commits internally and can never be
        composed into a larger transaction (MVP-19A's own governance
        finding)."""
        if not metric_values:
            raise ProvenanceMismatchError("At least one metric value is required.")

        entry = self.entries.create(
            campaign=campaign, period_start=period_start, period_end=period_end,
            channel=channel, source=source, client_request_id=client_request_id,
        )
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
        return entry

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
        try:
            entry = self._record_metric_entry_uncommitted(
                campaign=campaign, period_start=period_start, period_end=period_end,
                channel=channel, source=source, client_request_id=client_request_id,
                metric_values=metric_values, is_correction=is_correction,
                actor_user_id=actor_user_id, request_id=request_id,
            )
        except IntegrityError:
            self.session.rollback()
            existing = self.entries.get_by_workspace_and_request_id(
                workspace_id=campaign.workspace_id, client_request_id=client_request_id
            )
            if existing is not None:
                return existing
            raise
        self.session.commit()
        return entry

    # --- derived entities: service-layer only ----------------------------
    #
    # MVP-11B (MVP-11A-R3 §E/§F/§G Governance repair): each of the three
    # public methods below is split into a private, non-committing
    # primitive (all the actual validation/creation/audit work, ending in
    # flush() only) plus a thin public wrapper that calls the primitive
    # and then commits — reproducing today's exact external behavior for
    # every existing caller, byte for byte. This is a pure internal
    # refactor: the published signature, return value, and commit
    # semantics of record_observation/record_signal/record_analysis_result
    # are completely unchanged. The new MeasurementAnalysisService (MVP-11B)
    # calls the private primitives directly, inside its own
    # session.begin_nested() savepoints, so it can own its own transaction
    # boundary — something impossible if it only had the committing public
    # methods to call (a public method's own internal commit() cannot be
    # undone by a savepoint opened around the call to it).

    def _record_observation_uncommitted(
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
        return observation

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
        observation = self._record_observation_uncommitted(
            campaign=campaign,
            metric_entries=metric_entries,
            metric_name=metric_name,
            value=value,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return observation

    def _record_signal_uncommitted(
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
        return signal

    def record_signal(
        self,
        *,
        campaign: Campaign,
        observations: list[PerformanceObservation],
        summary: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> PerformanceSignal:
        signal = self._record_signal_uncommitted(
            campaign=campaign, observations=observations, summary=summary,
            actor_user_id=actor_user_id, request_id=request_id,
        )
        self.session.commit()
        return signal

    def _record_analysis_result_uncommitted(
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
        return analysis_result

    def record_analysis_result(
        self,
        *,
        campaign: Campaign,
        signals: list[PerformanceSignal],
        summary: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> AnalysisResult:
        analysis_result = self._record_analysis_result_uncommitted(
            campaign=campaign, signals=signals, summary=summary,
            actor_user_id=actor_user_id, request_id=request_id,
        )
        self.session.commit()
        return analysis_result

    # --- Distribution-linked Measurement Evidence — MVP-19B --------------
    #
    # CORE SEMANTIC (frozen): "the user reported these metrics for this
    # Distribution" — never "this Distribution generated/caused these
    # metrics." Eligibility (ContentPiece/ContentDistribution == DISTRIBUTED)
    # is verified by the caller (the router, which already holds both rows
    # from the Content bounded context) before either method below is
    # invoked — this service never queries ContentPiece.status itself.

    def _validate_evidence_period(self, *, distribution: ContentDistribution, period_end: date) -> None:
        if distribution.distributed_at is not None and period_end < distribution.distributed_at.date():
            raise EvidencePeriodInvalidError()

    def _existing_evidence_create_match(
        self,
        *,
        existing_entry: MetricEntry,
        distribution: ContentDistribution,
        period_start: date,
        period_end: date,
        metric_values: dict[str, Decimal],
        source_reference: str | None,
    ) -> DistributionMetricEvidence | None:
        """MVP-19B §26/§28: an exact logical replay of a CREATE request —
        same resolved Distribution, same period, same canonical value map,
        same ``source_reference``. Comparison is by canonical persisted
        value (a Decimal-vs-Decimal equality, independent of scale/exponent
        and of the request's own key order), never by raw serialization."""
        evidence = self.evidence.get_by_metric_entry_id(existing_entry.id)
        if evidence is None or evidence.distribution_id != distribution.id:
            return None
        if existing_entry.period_start != period_start or existing_entry.period_end != period_end:
            return None
        if existing_entry.channel != distribution.channel or existing_entry.source is not MetricSource.MANUAL:
            return None
        if evidence.source_reference != source_reference:
            return None
        existing_values = {v.metric_name: v.value for v in self.values.list_for_entry(existing_entry.id)}
        if existing_values.keys() != metric_values.keys() or any(
            existing_values[name] != metric_values[name] for name in metric_values
        ):
            return None
        return evidence

    def create_distribution_evidence(
        self,
        *,
        distribution: ContentDistribution,
        campaign: Campaign,
        period_start: date,
        period_end: date,
        metric_values: dict[str, Decimal],
        client_request_id: str,
        source_reference: str | None,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> tuple[DistributionMetricEvidence, bool]:
        """Returns ``(evidence, created)`` — ``created`` is ``False`` for
        both a fast-path idempotent replay and a race-lost-but-identical
        replay, ``True`` only for a genuinely new row. Raises
        ``IdempotencyKeyConflictError`` (409) if ``client_request_id`` was
        already used for a different logical request — including one that
        was not even Evidence at all (the key space is shared with the
        plain ``MetricEntry.client_request_id`` uniqueness MVP-19B §25
        deliberately reuses)."""
        self._validate_evidence_period(distribution=distribution, period_end=period_end)

        existing_entry = self.entries.get_by_workspace_and_request_id(
            workspace_id=distribution.workspace_id, client_request_id=client_request_id
        )
        if existing_entry is not None:
            match = self._existing_evidence_create_match(
                existing_entry=existing_entry, distribution=distribution, period_start=period_start,
                period_end=period_end, metric_values=metric_values, source_reference=source_reference,
            )
            if match is None:
                raise IdempotencyKeyConflictError()
            return match, False

        try:
            with self.session.begin_nested():
                entry = self._record_metric_entry_uncommitted(
                    campaign=campaign, period_start=period_start, period_end=period_end,
                    channel=distribution.channel, source=MetricSource.MANUAL,
                    client_request_id=client_request_id, metric_values=metric_values, is_correction=False,
                    actor_user_id=actor_user_id, request_id=request_id,
                )
                evidence = self.evidence.create(
                    distribution=distribution, metric_entry=entry, created_by_user_id=actor_user_id,
                    source_reference=source_reference,
                )
                self.events.record(
                    workspace_id=distribution.workspace_id, event_type=EVENT_DISTRIBUTION_EVIDENCE_RECORDED,
                    actor_type=ActorType.USER, campaign_id=campaign.id, metric_entry_id=entry.id,
                    distribution_id=distribution.id, distribution_metric_evidence_id=evidence.id,
                    actor_user_id=actor_user_id, request_id=request_id,
                )
        except IntegrityError:
            # Lost the UNIQUE(workspace_id, client_request_id) race on
            # MetricEntry — the whole savepoint (entry + values + Evidence
            # + audit) rolled back together; the winner's row is the only
            # thing that can legitimately be found under this same key now.
            existing_entry = self.entries.get_by_workspace_and_request_id(
                workspace_id=distribution.workspace_id, client_request_id=client_request_id
            )
            if existing_entry is None:
                raise
            match = self._existing_evidence_create_match(
                existing_entry=existing_entry, distribution=distribution, period_start=period_start,
                period_end=period_end, metric_values=metric_values, source_reference=source_reference,
            )
            if match is None:
                raise IdempotencyKeyConflictError() from None
            self.session.commit()
            return match, False
        else:
            self.session.commit()
            return evidence, True

    def _existing_evidence_correction_match(
        self,
        *,
        existing_entry: MetricEntry,
        distribution: ContentDistribution,
        target_evidence_public_id: str,
        period_start: date,
        period_end: date,
        metric_values: dict[str, Decimal],
        source_reference: str | None,
        correction_reason: str,
    ) -> DistributionMetricEvidence | None:
        evidence = self.evidence.get_by_metric_entry_id(existing_entry.id)
        if evidence is None or evidence.distribution_id != distribution.id or evidence.supersedes_evidence_id is None:
            return None
        target = self.evidence.get_by_id(evidence.supersedes_evidence_id)
        if target is None or target.public_id != target_evidence_public_id:
            return None
        if existing_entry.period_start != period_start or existing_entry.period_end != period_end:
            return None
        if existing_entry.channel != distribution.channel or existing_entry.source is not MetricSource.MANUAL:
            return None
        if evidence.source_reference != source_reference or evidence.correction_reason != correction_reason:
            return None
        existing_values = {v.metric_name: v.value for v in self.values.list_for_entry(existing_entry.id)}
        if existing_values.keys() != metric_values.keys() or any(
            existing_values[name] != metric_values[name] for name in metric_values
        ):
            return None
        return evidence

    def create_distribution_evidence_correction(
        self,
        *,
        distribution: ContentDistribution,
        campaign: Campaign,
        target_evidence_public_id: str,
        period_start: date,
        period_end: date,
        metric_values: dict[str, Decimal],
        client_request_id: str,
        source_reference: str | None,
        correction_reason: str,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> tuple[DistributionMetricEvidence, bool]:
        """MVP-19B §27 (mandatory ordering): idempotency resolution happens
        BEFORE the target is resolved/locked — an exact successful replay
        of a prior correction must return that correction's Evidence with
        200, never a stale-target 409, even though by the time of the
        replay the original target is no longer the current leaf (a LATER,
        different correction may since have superseded it further)."""
        self._validate_evidence_period(distribution=distribution, period_end=period_end)

        existing_entry = self.entries.get_by_workspace_and_request_id(
            workspace_id=distribution.workspace_id, client_request_id=client_request_id
        )
        if existing_entry is not None:
            match = self._existing_evidence_correction_match(
                existing_entry=existing_entry, distribution=distribution,
                target_evidence_public_id=target_evidence_public_id, period_start=period_start,
                period_end=period_end, metric_values=metric_values, source_reference=source_reference,
                correction_reason=correction_reason,
            )
            if match is None:
                raise IdempotencyKeyConflictError()
            return match, False

        # No replay found — only now resolve and lock the target, verify
        # it is still the current leaf, and create the successor.
        target = self.evidence.get_by_public_id(target_evidence_public_id, for_update=True)
        if target is None or target.distribution_id != distribution.id:
            raise ForbiddenError()
        if self.evidence.get_successor(target.id) is not None:
            raise EvidenceCorrectionTargetStaleError()

        try:
            with self.session.begin_nested():
                entry = self._record_metric_entry_uncommitted(
                    campaign=campaign, period_start=period_start, period_end=period_end,
                    channel=distribution.channel, source=MetricSource.MANUAL,
                    client_request_id=client_request_id, metric_values=metric_values, is_correction=True,
                    actor_user_id=actor_user_id, request_id=request_id,
                )
                evidence = self.evidence.create(
                    distribution=distribution, metric_entry=entry, created_by_user_id=actor_user_id,
                    source_reference=source_reference, supersedes=target, correction_reason=correction_reason,
                )
                self.events.record(
                    workspace_id=distribution.workspace_id, event_type=EVENT_DISTRIBUTION_EVIDENCE_CORRECTED,
                    actor_type=ActorType.USER, campaign_id=campaign.id, metric_entry_id=entry.id,
                    distribution_id=distribution.id, distribution_metric_evidence_id=evidence.id,
                    actor_user_id=actor_user_id, request_id=request_id,
                )
        except IntegrityError:
            existing_entry = self.entries.get_by_workspace_and_request_id(
                workspace_id=distribution.workspace_id, client_request_id=client_request_id
            )
            if existing_entry is not None:
                match = self._existing_evidence_correction_match(
                    existing_entry=existing_entry, distribution=distribution,
                    target_evidence_public_id=target_evidence_public_id, period_start=period_start,
                    period_end=period_end, metric_values=metric_values, source_reference=source_reference,
                    correction_reason=correction_reason,
                )
                if match is not None:
                    self.session.commit()
                    return match, False
                raise IdempotencyKeyConflictError() from None
            # Our own client_request_id was never persisted at all — this
            # was not a key collision, it was the single-successor race
            # (UNIQUE(supersedes_evidence_id)) against a different,
            # concurrent correction that already claimed this same leaf.
            raise EvidenceCorrectionTargetStaleError() from None
        else:
            self.session.commit()
            return evidence, True

    def list_distribution_evidence(
        self, *, distribution_id: uuid.UUID, limit: int, offset: int
    ) -> tuple[list[tuple[DistributionMetricEvidence, MetricEntry, list[MetricValue]]], int, set[uuid.UUID]]:
        items, total = self.evidence.list_for_distribution(distribution_id=distribution_id, limit=limit, offset=offset)
        values_by_entry_id: dict[uuid.UUID, list[MetricValue]] = {}
        if items:
            metric_entry_ids = [row.metric_entry_id for row in items]
            for value in self.values.list_for_entries(metric_entry_ids):
                values_by_entry_id.setdefault(value.metric_entry_id, []).append(value)
        rows = [
            (row, self.entries.get_by_id(row.metric_entry_id), values_by_entry_id.get(row.metric_entry_id, []))
            for row in items
        ]
        superseded_ids = self.evidence.list_superseded_ids_for_distribution(distribution_id)
        return rows, total, superseded_ids

    def summarize_distribution_evidence(
        self, *, distribution_id: uuid.UUID
    ) -> list[tuple[DistributionMetricEvidence, MetricEntry, list[MetricValue]]]:
        """MVP-21/MVP-21A-R1: the current-Evidence set for the summary,
        resolved in exactly one authoritative statement
        (``list_current_for_distribution``) — no separate superseded-id
        fetch, no Python-side re-derivation of "current". Value loading is
        a second, independent batched query, safe because Evidence/
        MetricEntry/MetricValue rows are immutable once created (MVP-21A-R1
        §H) — a concurrent correction can only ever append new rows, never
        alter the ones this method already selected."""
        current = self.evidence.list_current_for_distribution(distribution_id)
        values_by_entry_id: dict[uuid.UUID, list[MetricValue]] = {}
        if current:
            metric_entry_ids = [row.metric_entry_id for row in current]
            for value in self.values.list_for_entries(metric_entry_ids):
                values_by_entry_id.setdefault(value.metric_entry_id, []).append(value)
        return [
            (row, self.entries.get_by_id(row.metric_entry_id), values_by_entry_id.get(row.metric_entry_id, []))
            for row in current
        ]

    def summarize_distribution_evidence_for_campaign(
        self, *, campaign_id: uuid.UUID
    ) -> list[tuple[DistributionMetricEvidence, ContentDistribution, ContentPiece, MetricEntry, list[MetricValue]]]:
        """MVP-22/MVP-22A §I/§M: the Campaign-wide current-Evidence set for
        the Campaign Distribution Evidence Rollup, resolved in exactly one
        authoritative statement (``list_current_for_campaign``) spanning
        every eligible Distribution in the Campaign — no per-Distribution
        loop, no separate superseded-id fetch. ``MetricEntry``/
        ``MetricValue`` loading is two further batched queries (bounded by
        the total current-Evidence set, never by Distribution count), safe
        for the same immutability reason ``summarize_distribution_evidence``
        already relies on. ContentVersion public-id resolution is
        deliberately NOT done here — cross-domain lookups belong to the
        router, matching this service's own existing convention of
        accepting already-resolved Content/Distribution objects rather
        than reaching into another domain's repository itself."""
        current = self.evidence.list_current_for_campaign(campaign_id)
        if not current:
            return []
        metric_entry_ids = [evidence.metric_entry_id for evidence, _distribution, _piece in current]
        entries_by_id = {entry.id: entry for entry in self.entries.list_for_ids(metric_entry_ids)}
        values_by_entry_id: dict[uuid.UUID, list[MetricValue]] = {}
        for value in self.values.list_for_entries(metric_entry_ids):
            values_by_entry_id.setdefault(value.metric_entry_id, []).append(value)
        return [
            (
                evidence,
                distribution,
                piece,
                entries_by_id[evidence.metric_entry_id],
                values_by_entry_id.get(evidence.metric_entry_id, []),
            )
            for evidence, distribution, piece in current
        ]
