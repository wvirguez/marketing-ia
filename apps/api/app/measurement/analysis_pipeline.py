"""Measurement Analysis Run — deterministic derivation pipeline (MVP-11B).

Implements exactly the architecture frozen across MVP-11A / -R1 / -R2 / -R3:
MetricEntry -> PerformanceObservation -> PerformanceSignal -> AnalysisResult,
triggered by a future explicit ``POST /campaigns/{id}/analysis/run`` (not
part of MVP-11B — this module has no public route yet). No LLM, no
provider, no Learning write, no orchestration/CampaignRun/RunStageExecution
mutation, no BusinessStage.MEASUREMENT wiring.

TRANSACTION OWNERSHIP (MVP-11A-R3): this service owns its own transaction
boundary. It never calls the generic public ``record_observation``/
``record_signal``/``record_analysis_result`` (each of which commits
internally on its own) — it calls their private, non-committing
``_record_*_uncommitted`` primitives instead, wrapped in its own
``session.begin_nested()`` savepoints, so a lost natural-identity race can
roll back exactly the losing attempt (core row, generic associations, and
AuditEvent together) without disturbing anything else already committed in
this run or any other. One real ``session.commit()`` happens per
fully-assembled atomic unit (run+snapshot+started, each Observation unit,
each Signal unit, the optional AnalysisResult unit, and the terminal
COMPLETED/FAILED transition) — maximizing how much valid evidence survives
an unexpected process crash, per the accepted, documented RESERVATION 3
limitation (stale RUNNING run recovery is explicitly out of scope).

PROVENANCE (MVP-11A-R2): the four frozen Measurement core entities
(MetricEntry, PerformanceObservation, PerformanceSignal, AnalysisResult)
are never modified — no pipeline-specific column is added to any of them,
and their existing generic multi-source capability remains fully valid.
Every pipeline-specific concept (run, input snapshot, Observation/Signal
natural identity + creator, Observation/Signal usage, AnalysisResult
ownership) lives entirely in the seven new tables this stage introduces.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.measurement.models import (
    AnalysisResult,
    MeasurementAnalysisRun,
    MeasurementAnalysisRunStatus,
    MetricEntry,
    PerformanceObservation,
    PerformanceSignal,
)
from app.measurement.repository import (
    MeasurementAnalysisRunMetricEntryRepository,
    MeasurementAnalysisRunObservationUsageRepository,
    MeasurementAnalysisRunRepository,
    MeasurementAnalysisRunResultRepository,
    MeasurementAnalysisRunSignalUsageRepository,
    MeasurementObservationDerivationRepository,
    MeasurementSignalDerivationRepository,
)
from app.measurement.service import MeasurementService

EVENT_ANALYSIS_RUN_STARTED = "measurement.analysis_run.started"
EVENT_ANALYSIS_RUN_COMPLETED = "measurement.analysis_run.completed"
EVENT_ANALYSIS_RUN_FAILED = "measurement.analysis_run.failed"

# Public-safe only (MVP-11A-R3 §Z) — never raw exception/SQL/driver text,
# tracebacks, or internal identifiers. The real cause is available server-side
# only via the structured logging middleware's own request correlation.
_PUBLIC_SAFE_FAILURE_MESSAGE = "No se pudo completar el análisis en este momento."

_CurrentEntry = tuple[MetricEntry, dict[str, Decimal]]


def _is_earlier_period(candidate: MetricEntry, *, than: MetricEntry) -> bool:
    """A candidate qualifies as a genuinely earlier, non-overlapping period
    only if its own period ends strictly before the target period begins
    (MVP-11B §23) — never a same-period correction (which shares the exact
    same period_start/period_end) and never an ambiguously overlapping one."""
    return candidate.period_end < than.period_start


class MeasurementAnalysisService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.measurement = MeasurementService(session)
        self.runs = MeasurementAnalysisRunRepository(session)
        self.run_entries = MeasurementAnalysisRunMetricEntryRepository(session)
        self.observation_derivations = MeasurementObservationDerivationRepository(session)
        self.signal_derivations = MeasurementSignalDerivationRepository(session)
        self.observation_usages = MeasurementAnalysisRunObservationUsageRepository(session)
        self.signal_usages = MeasurementAnalysisRunSignalUsageRepository(session)
        self.run_results = MeasurementAnalysisRunResultRepository(session)
        self.events = AuditEventRepository(session)

    # --- public entry point ------------------------------------------------

    def run_analysis(
        self,
        *,
        campaign: Campaign,
        client_request_id: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> MeasurementAnalysisRun:
        """Idempotent by construction, exactly like MetricEntry (MVP-11A-R1
        §6/§18/§22): a request sharing the same ``(workspace_id,
        client_request_id)`` as an existing run — RUNNING, COMPLETED, or
        FAILED — returns that run's own current state unchanged. It never
        executes again, never resumes, never mutates the existing run.
        Trying again always means a NEW run with a NEW client_request_id."""
        run, current_entries, is_new_execution = self._start_run(
            campaign=campaign, client_request_id=client_request_id, actor_user_id=actor_user_id, request_id=request_id
        )
        if not is_new_execution:
            return run

        try:
            observations = self._derive_observations(
                campaign=campaign, run=run, current_entries=current_entries,
                actor_user_id=actor_user_id, request_id=request_id,
            )
            fresh_signals = self._derive_signals(
                campaign=campaign, run=run, current_entries=current_entries, observations=observations,
                actor_user_id=actor_user_id, request_id=request_id,
            )
            if fresh_signals:
                self._create_analysis_result(
                    campaign=campaign, run=run, fresh_signals=fresh_signals,
                    actor_user_id=actor_user_id, request_id=request_id,
                )
            self._complete_run(run=run, actor_user_id=actor_user_id, request_id=request_id)
        except Exception:
            self._fail_run(run=run, actor_user_id=actor_user_id, request_id=request_id)
            raise
        return run

    # --- run start: one atomic unit (run + full snapshot + started audit) -

    def _start_run(
        self,
        *,
        campaign: Campaign,
        client_request_id: str,
        actor_user_id: uuid.UUID | None,
        request_id: str | None,
    ) -> tuple[MeasurementAnalysisRun, list[_CurrentEntry], bool]:
        try:
            run = self.runs.create(campaign=campaign, client_request_id=client_request_id)
        except IntegrityError:
            self.session.rollback()
            existing = self.runs.get_by_workspace_and_request_id(
                workspace_id=campaign.workspace_id, client_request_id=client_request_id
            )
            if existing is not None:
                return existing, [], False
            raise

        try:
            current_entries = self._select_current_metric_entries(campaign)
            self.run_entries.create_many(run=run, metric_entries=[entry for entry, _values in current_entries])
            self.events.record(
                workspace_id=campaign.workspace_id,
                event_type=EVENT_ANALYSIS_RUN_STARTED,
                actor_type=ActorType.USER if actor_user_id is not None else ActorType.SYSTEM,
                campaign_id=campaign.id,
                measurement_analysis_run_id=run.id,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
        except Exception:
            # No RUNNING run may survive with an incomplete snapshot
            # (MVP-11A-R3 §K/§13) — roll back the run row's own insert too.
            self.session.rollback()
            raise

        self.session.commit()
        return run, current_entries, True

    def _select_current_metric_entries(self, campaign: Campaign) -> list[_CurrentEntry]:
        """Reuses MetricEntry.is_current's exact existing computation
        (MVP-11B §19) — never a divergent definition."""
        rows = self.measurement.list_metric_entries_for_campaign(campaign_id=campaign.id)
        return [
            (entry, {value.metric_name: value.value for value in values})
            for entry, values, is_current in rows
            if is_current
        ]

    # --- Observation derivation ---------------------------------------------

    def _derive_observations(
        self,
        *,
        campaign: Campaign,
        run: MeasurementAnalysisRun,
        current_entries: list[_CurrentEntry],
        actor_user_id: uuid.UUID | None,
        request_id: str | None,
    ) -> dict[tuple[uuid.UUID, str], PerformanceObservation]:
        observations: dict[tuple[uuid.UUID, str], PerformanceObservation] = {}
        for entry, values in current_entries:
            for metric_name, value in values.items():
                observations[(entry.id, metric_name)] = self._derive_one_observation(
                    campaign=campaign, run=run, source_entry=entry, metric_name=metric_name, value=value,
                    actor_user_id=actor_user_id, request_id=request_id,
                )
        return observations

    def _derive_one_observation(
        self,
        *,
        campaign: Campaign,
        run: MeasurementAnalysisRun,
        source_entry: MetricEntry,
        metric_name: str,
        value: Decimal,
        actor_user_id: uuid.UUID | None,
        request_id: str | None,
    ) -> PerformanceObservation:
        # Fast path (MVP-11B §21): an existing derivation means reuse, never
        # a duplicate — no new `.recorded` audit event, only a usage row.
        existing = self.observation_derivations.get_by_identity(
            source_metric_entry_id=source_entry.id, metric_name=metric_name
        )
        if existing is not None:
            observation = self.measurement.observations.get_by_id(existing.observation_id)
            self.observation_usages.create(run=run, observation=observation)
            self.session.commit()
            return observation

        # Race path (MVP-11B §22): the whole unit — core row, its generic
        # ObservationMetricEntry association, its AuditEvent, this
        # pipeline's derivation row, and this run's usage row — lives in
        # one SAVEPOINT. A lost UNIQUE race rolls all of it back together,
        # leaving no orphan core row and no false AuditEvent.
        try:
            with self.session.begin_nested():
                observation = self.measurement._record_observation_uncommitted(
                    campaign=campaign, metric_entries=[source_entry], metric_name=metric_name, value=value,
                    actor_user_id=actor_user_id, request_id=request_id,
                )
                self.observation_derivations.create(
                    observation=observation, source_metric_entry_id=source_entry.id, metric_name=metric_name,
                    creator_run=run,
                )
                self.observation_usages.create(run=run, observation=observation)
        except IntegrityError:
            winner = self.observation_derivations.get_by_identity(
                source_metric_entry_id=source_entry.id, metric_name=metric_name
            )
            assert winner is not None  # the race can only be lost to a winner that committed this exact identity
            observation = self.measurement.observations.get_by_id(winner.observation_id)
            self.observation_usages.create(run=run, observation=observation)
            self.session.commit()
            return observation
        else:
            self.session.commit()
            return observation

    # --- Signal derivation ---------------------------------------------------

    def _derive_signals(
        self,
        *,
        campaign: Campaign,
        run: MeasurementAnalysisRun,
        current_entries: list[_CurrentEntry],
        observations: dict[tuple[uuid.UUID, str], PerformanceObservation],
        actor_user_id: uuid.UUID | None,
        request_id: str | None,
    ) -> list[PerformanceSignal]:
        fresh_signals: list[PerformanceSignal] = []
        for entry, values in current_entries:
            for metric_name, current_value in values.items():
                baseline = self._select_baseline(current_entries, entry=entry, metric_name=metric_name)
                if baseline is None:
                    continue
                baseline_entry, prior_value = baseline
                baseline_derivation = self.observation_derivations.get_by_identity(
                    source_metric_entry_id=baseline_entry.id, metric_name=metric_name
                )
                if baseline_derivation is None:
                    # Never fabricate a baseline Observation on the fly
                    # (MVP-11B §23) — if one doesn't already exist, there
                    # is no Signal this run.
                    continue

                current_observation = observations[(entry.id, metric_name)]
                prior_observation_id = baseline_derivation.observation_id
                signal, created = self._derive_one_signal(
                    campaign=campaign, run=run,
                    current_observation=current_observation, prior_observation_id=prior_observation_id,
                    metric_name=metric_name, channel=entry.channel,
                    current_value=current_value, prior_value=prior_value,
                    current_period=(entry.period_start, entry.period_end),
                    prior_period=(baseline_entry.period_start, baseline_entry.period_end),
                    actor_user_id=actor_user_id, request_id=request_id,
                )
                if created:
                    fresh_signals.append(signal)
        return fresh_signals

    def _select_baseline(
        self, current_entries: list[_CurrentEntry], *, entry: MetricEntry, metric_name: str
    ) -> tuple[MetricEntry, Decimal] | None:
        """Same metric_name, same channel, genuinely earlier and
        unambiguous period, and the candidate must itself carry a value
        for this exact metric_name (MVP-11B §23). Picks the chronologically
        nearest qualifying period; a same-period correction never
        qualifies (its period_end is never < the target's period_start)."""
        candidates = [
            (candidate, candidate_values[metric_name])
            for candidate, candidate_values in current_entries
            if candidate.id != entry.id
            and candidate.channel == entry.channel
            and metric_name in candidate_values
            and _is_earlier_period(candidate, than=entry)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda pair: pair[0].period_end)

    def _derive_one_signal(
        self,
        *,
        campaign: Campaign,
        run: MeasurementAnalysisRun,
        current_observation: PerformanceObservation,
        prior_observation_id: uuid.UUID,
        metric_name: str,
        channel: str,
        current_value: Decimal,
        prior_value: Decimal,
        current_period: tuple[date, date],
        prior_period: tuple[date, date],
        actor_user_id: uuid.UUID | None,
        request_id: str | None,
    ) -> tuple[PerformanceSignal, bool]:
        existing = self.signal_derivations.get_by_identity(
            current_observation_id=current_observation.id, prior_observation_id=prior_observation_id
        )
        if existing is not None:
            signal = self.measurement.signals.get_by_id(existing.signal_id)
            self.signal_usages.create(run=run, signal=signal)
            self.session.commit()
            return signal, False

        prior_observation = self.measurement.observations.get_by_id(prior_observation_id)
        summary = self._build_signal_summary(
            metric_name=metric_name, channel=channel, current_value=current_value, prior_value=prior_value,
            current_period=current_period, prior_period=prior_period,
        )
        try:
            with self.session.begin_nested():
                signal = self.measurement._record_signal_uncommitted(
                    campaign=campaign, observations=[current_observation, prior_observation], summary=summary,
                    actor_user_id=actor_user_id, request_id=request_id,
                )
                self.signal_derivations.create(
                    signal=signal, current_observation_id=current_observation.id,
                    prior_observation_id=prior_observation_id, creator_run=run,
                )
                self.signal_usages.create(run=run, signal=signal)
        except IntegrityError:
            winner = self.signal_derivations.get_by_identity(
                current_observation_id=current_observation.id, prior_observation_id=prior_observation_id
            )
            assert winner is not None
            signal = self.measurement.signals.get_by_id(winner.signal_id)
            self.signal_usages.create(run=run, signal=signal)
            self.session.commit()
            return signal, False
        else:
            self.session.commit()
            return signal, True

    @staticmethod
    def _build_signal_summary(
        *,
        metric_name: str,
        channel: str,
        current_value: Decimal,
        prior_value: Decimal,
        current_period: tuple[date, date],
        prior_period: tuple[date, date],
    ) -> str:
        """Deterministic, strictly neutral (MVP-11B §24): only
        increased/decreased/unchanged — never improved/worsened/better/
        worse/significant/winner/proven/causal/optimized/validated. The
        system has no directional-goodness metadata for an arbitrary,
        open-vocabulary metric name, so it never guesses one."""
        if current_value > prior_value:
            trend = "increased"
        elif current_value < prior_value:
            trend = "decreased"
        else:
            trend = "remained unchanged"
        return (
            f"{metric_name} {trend} for {channel}: {prior_value} "
            f"({prior_period[0].isoformat()} to {prior_period[1].isoformat()}) to {current_value} "
            f"({current_period[0].isoformat()} to {current_period[1].isoformat()})."
        )

    # --- AnalysisResult ------------------------------------------------------

    def _create_analysis_result(
        self,
        *,
        campaign: Campaign,
        run: MeasurementAnalysisRun,
        fresh_signals: list[PerformanceSignal],
        actor_user_id: uuid.UUID | None,
        request_id: str | None,
    ) -> AnalysisResult:
        """At most one per run (MVP-11B §27/§28), created only from the
        Signals this run genuinely created — reused Signals alone never
        justify a new AnalysisResult (MVP-11A-R1 §Q). No natural-identity
        race exists for a given run's own AnalysisResult (only the single
        execution that owns this run ever reaches this point), so no
        IntegrityError-catch/retry loop is used here — the UNIQUE
        constraints on measurement_analysis_run_results remain as a
        defensive DB-level guarantee regardless."""
        summary = self._build_analysis_result_summary(run=run, signals=fresh_signals)
        with self.session.begin_nested():
            analysis_result = self.measurement._record_analysis_result_uncommitted(
                campaign=campaign, signals=fresh_signals, summary=summary,
                actor_user_id=actor_user_id, request_id=request_id,
            )
            self.run_results.create(run=run, analysis_result=analysis_result)
        self.session.commit()
        return analysis_result

    @staticmethod
    def _build_analysis_result_summary(*, run: MeasurementAnalysisRun, signals: list[PerformanceSignal]) -> str:
        """Deterministic, rule-based, structured-to-text (MVP-11A §O): a
        flat restatement of this run's own already-neutral Signal text —
        never an LLM call, never a reasoning trace, never a
        recommendation, learning claim, or strategic judgment."""
        findings = "; ".join(signal.summary for signal in signals)
        return f"Analysis run {run.public_id} identified {len(signals)} performance signal(s): {findings}."

    # --- run completion / failure --------------------------------------------

    def _complete_run(
        self, *, run: MeasurementAnalysisRun, actor_user_id: uuid.UUID | None, request_id: str | None
    ) -> None:
        """A run with zero Signals and zero AnalysisResult may still be
        COMPLETED (MVP-11A-R3 §M) — absence of a comparison baseline, or
        nothing new to report, is never a failure."""
        run.status = MeasurementAnalysisRunStatus.COMPLETED
        run.completed_at = datetime.now(timezone.utc)
        self.events.record(
            workspace_id=run.workspace_id,
            event_type=EVENT_ANALYSIS_RUN_COMPLETED,
            actor_type=ActorType.USER if actor_user_id is not None else ActorType.SYSTEM,
            campaign_id=run.campaign_id,
            measurement_analysis_run_id=run.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()

    def _fail_run(
        self, *, run: MeasurementAnalysisRun, actor_user_id: uuid.UUID | None, request_id: str | None
    ) -> None:
        """Retains every earlier checkpointed valid evidence unit
        untouched (MVP-11A-R3 §S) — only the run's own row transitions to
        FAILED, with a sanitized, public-safe failure_reason (never raw
        SQL/driver/traceback text)."""
        self.session.rollback()
        failed_run = self.runs.get_by_id(run.id)
        assert failed_run is not None  # committed durably by _start_run before any of this could run
        failed_run.status = MeasurementAnalysisRunStatus.FAILED
        failed_run.failure_reason = _PUBLIC_SAFE_FAILURE_MESSAGE
        failed_run.completed_at = datetime.now(timezone.utc)
        self.events.record(
            workspace_id=failed_run.workspace_id,
            event_type=EVENT_ANALYSIS_RUN_FAILED,
            actor_type=ActorType.USER if actor_user_id is not None else ActorType.SYSTEM,
            campaign_id=failed_run.campaign_id,
            measurement_analysis_run_id=failed_run.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
