"""Experiment Measurement — the single writer of ``ExperimentMeasurementRun``
and its three pure children (frozen Discovery / Design Freeze / Pre-
Implementation Reconciliation / Final Relational Integrity Reconciliation).

CAPABILITY: computes and persists ONE immutable, historical, OBSERVATIONAL
reading of the evidence ACTIVELY claimed under ONE started execution attempt
(the Start), applying PEMD's frozen semantics-version-1 rules for the first
time anywhere in this codebase. MEASUREMENT != RESULT != WINNER != HYPOTHESIS
VERDICT != EXPERIMENTAL VALIDITY != CAUSALITY != LEARNING. Nothing here
writes any table other than ``experiment_measurement_runs``/
``_signal_outputs``/``_slice_outputs``/``_datum_usages`` and ``audit_events``.
No Allocation/Assignment/Exposure/Variant-arm evidence, no
statistical/significance/lift language, no external side effect of any kind.

TRANSACTION (frozen Reconciliation §H/§19): phase 1 resolves Campaign (by the
caller) / Experiment / Start / Authorization under the session's ordinary,
default-isolation transaction, then that transaction ends cleanly
(``session.commit()`` — read-only, nothing pending). Phase 2 establishes a
FRESH REPEATABLE READ transaction — ``session.connection(execution_options=
{"isolation_level": "REPEATABLE READ"})``, called as the very first
statement-triggering operation of the new transaction, which is legal only
because phase 1 already ended — under which every input read (Start re-read,
Contract, RequiredSignals, active Claims, MetricEntry/MetricValue, later-
grouping-entry facts) shares one consistent, authoritative snapshot, before
the whole Run + children + one audit event are inserted atomically.

IDEMPOTENCY: ``UNIQUE(workspace_id, client_request_id)`` on the Run alone.
Material mismatch is exactly a different ``start_id`` — every other identity
(Authorization, Contract, semantics version) is derived from the Start.
23505 -> rollback -> fresh default-isolation transaction -> canonical lookup
by key -> compare ``start_id`` -> replay (same Start) or typed 409 (different
Start); nothing is ever recomputed on replay. 40001 -> rollback -> entirely
fresh transaction with a fresh REPEATABLE READ snapshot -> re-run the WHOLE
pipeline once, same ``client_request_id``; a second 40001 surfaces a typed,
retryable error rather than ever being treated as an idempotency conflict.

INPUT SET (frozen Reconciliation §L/§13): Start-scoped, ``disposed_at IS
NULL`` AS VISIBLE IN THAT SNAPSHOT. A claim disposed as of the snapshot is
simply absent — never a separate "excluded" usage row (Reconciliation R3,
``claim_disposed_at_run`` does not exist). A claim disposed AFTER the
snapshot still produces its ``DatumUsage`` row, unaltered, forever — this or
any other historical Run is never mutated by a later disposal, correction, or
new claim.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.core.api_errors import (
    ExperimentMeasurementIdempotencyKeyConflictError,
    ExperimentMeasurementRetryableError,
    ForbiddenError,
)
from app.measurement.models import MetricEntry, MetricValue
from app.measurement.repository import MetricEntryRepository, MetricValueRepository
from app.strategy.experiment_measurement_temporal import (
    EXCLUDED_AMBIGUOUS,
    OUT_OF_WINDOW,
    QUALIFYING,
    QUALIFYING_BASELINE,
    QUALIFYING_OBSERVATION,
    classify_comparative,
    classify_descriptive,
    inclusive_period_length,
    periods_overlap,
)
from app.strategy.models import (
    ExecutionAuthorization,
    ExecutionStartAttestation,
    Experiment,
    ExperimentEvidenceClaim,
    ExperimentMeasurementRun,
    MeasurementContractRequiredSignal,
    MeasurementContractVersion,
)
from app.strategy.repository import (
    ExecutionAuthorizationRepository,
    ExecutionStartAttestationRepository,
    ExperimentEvidenceClaimRepository,
    ExperimentMeasurementRunRepository,
    ExperimentRepository,
    MeasurementContractRepository,
)

EVENT_MEASUREMENT_RUN_CREATED = "strategy.experiment_measurement_run.created"

_UQ_RUN_CLIENT_REQUEST_ID = "uq_measurement_runs_workspace_client_request_id"

_CHANNEL_BINDING_EXACT = "EXACT"
_DECLARATION_LEVEL_DESCRIPTIVE = "DESCRIPTIVE"


@dataclass
class _DatumWorking:
    claim: ExperimentEvidenceClaim
    entry: MetricEntry
    value: MetricValue | None
    signal: MeasurementContractRequiredSignal
    channel: str
    recorded_before_declaration: bool
    later_grouping_entry_exists: bool
    temporal_role: str | None = None
    usage_decision: str = "CONSUMED"


def _is_serialization_failure(exc: OperationalError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == "40001"


class ExperimentMeasurementService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.experiments = ExperimentRepository(session)
        self.starts = ExecutionStartAttestationRepository(session)
        self.authorizations = ExecutionAuthorizationRepository(session)
        self.contracts = MeasurementContractRepository(session)
        self.claims = ExperimentEvidenceClaimRepository(session)
        self.entries = MetricEntryRepository(session)
        self.values = MetricValueRepository(session)
        self.runs = ExperimentMeasurementRunRepository(session)
        self.events = AuditEventRepository(session)

    def create(
        self,
        *,
        campaign: Campaign,
        experiment_public_id: str,
        start_public_id: str,
        client_request_id: str,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> tuple[ExperimentMeasurementRun, bool]:
        """Returns ``(run, created)``; ``created`` is False for a matching
        replay. ``actor_user_id`` is required and the audit actor is always
        ``ActorType.USER``."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        experiment_id = experiment.id
        workspace_id = campaign.workspace_id

        start, authorization = self._resolve_start(
            experiment_id=experiment_id, workspace_id=workspace_id, start_public_id=start_public_id
        )
        start_id = start.id
        authorization_id = authorization.id
        contract_version_id = authorization.contract_version_id

        # Phase 1 is read-only; end it cleanly before phase 2 reconfigures isolation.
        self.session.commit()

        for attempt in (1, 2):
            try:
                return self._compute_and_insert(
                    workspace_id=workspace_id,
                    experiment_id=experiment_id,
                    authorization_id=authorization_id,
                    start_id=start_id,
                    contract_version_id=contract_version_id,
                    client_request_id=client_request_id,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )
            except OperationalError as exc:
                self.session.rollback()
                if not _is_serialization_failure(exc):
                    raise
                if attempt == 1:
                    continue
                raise ExperimentMeasurementRetryableError() from exc
        raise AssertionError("unreachable")  # pragma: no cover

    # --- phase 2: one REPEATABLE READ transaction -----------------------------

    def _compute_and_insert(
        self,
        *,
        workspace_id: uuid.UUID,
        experiment_id: uuid.UUID,
        authorization_id: uuid.UUID,
        start_id: uuid.UUID,
        contract_version_id: uuid.UUID,
        client_request_id: str,
        actor_user_id: uuid.UUID,
        request_id: str | None,
    ) -> tuple[ExperimentMeasurementRun, bool]:
        # Legal only because phase 1 already ended via commit(): this is the
        # first statement-triggering call of a brand-new transaction.
        self.session.connection(execution_options={"isolation_level": "REPEATABLE READ"})

        start = self.starts.get_by_id(start_id)
        if start is None:  # pragma: no cover - FK guarantees existence
            raise ForbiddenError()
        contract = self.contracts.get_by_id(contract_version_id)
        if contract is None:  # pragma: no cover - FK guarantees existence
            raise ForbiddenError()
        signals = self.contracts.list_signals_for_version(contract_version_id=contract_version_id)
        signals_by_id = {signal.id: signal for signal in signals}

        claims = self.claims.list_active_for_start(start_id=start_id, workspace_id=workspace_id)

        entry_ids = list({claim.metric_entry_id for claim in claims})
        entries_by_id = {entry.id: entry for entry in self.entries.list_for_ids(entry_ids)}
        values = self.values.list_for_entries(entry_ids)
        value_by_key = {(value.metric_entry_id, value.metric_name): value for value in values}
        later_grouping_by_entry = {
            entry_id: self.entries.exists_later_in_grouping(entries_by_id[entry_id])
            for entry_id in entry_ids
            if entry_id in entries_by_id
        }

        is_structured = contract.declaration_level is not None

        working = self._build_working_set(
            claims=claims,
            signals_by_id=signals_by_id,
            entries_by_id=entries_by_id,
            value_by_key=value_by_key,
            later_grouping_by_entry=later_grouping_by_entry,
            contract=contract,
            started_at=start.started_at,
            is_structured=is_structured,
        )
        self._apply_multi_signal_exclusion(working)
        if is_structured:
            self._apply_overlap_exclusion(working)

        try:
            run = self.runs.create(
                workspace_id=workspace_id,
                experiment_id=experiment_id,
                authorization_id=authorization_id,
                start_id=start_id,
                contract_version_id=contract_version_id,
                declaration_semantics_version=contract.declaration_semantics_version or 1,
                client_request_id=client_request_id,
                created_by_user_id=actor_user_id,
            )

            if is_structured:
                self._persist_structured_outputs(
                    run=run, signals=signals, declaration_level=contract.declaration_level, working=working
                )

            for item in working:
                self.runs.add_datum_usage(
                    run=run,
                    claim_id=item.claim.id,
                    required_signal_id=item.signal.id,
                    channel=item.channel,
                    temporal_role=item.temporal_role,
                    usage_decision=item.usage_decision,
                    recorded_before_declaration=item.recorded_before_declaration,
                    later_grouping_entry_exists_at_run=item.later_grouping_entry_exists,
                )

            self.events.record(
                workspace_id=workspace_id,
                event_type=EVENT_MEASUREMENT_RUN_CREATED,
                actor_type=ActorType.USER,
                experiment_id=experiment_id,
                execution_start_attestation_id=start_id,
                measurement_contract_id=contract_version_id,
                experiment_measurement_run_id=run.id,
                new_state=run.public_id,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
            self.session.commit()
            return run, True
        except IntegrityError as exc:
            self.session.rollback()
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint != _UQ_RUN_CLIENT_REQUEST_ID:
                raise
            # A single point lookup needs no multi-statement snapshot; reset
            # to the ordinary default isolation for this one fresh read.
            self.session.connection(execution_options={"isolation_level": "READ COMMITTED"})
            canonical = self.runs.get_by_workspace_and_request_id(
                workspace_id=workspace_id, client_request_id=client_request_id
            )
            self.session.commit()
            if canonical is None:  # pragma: no cover - defensive
                raise
            if canonical.start_id != start_id:
                raise ExperimentMeasurementIdempotencyKeyConflictError() from None
            return canonical, False

    # --- pipeline steps (frozen processing order, Design Freeze §T) ----------

    def _build_working_set(
        self,
        *,
        claims: list[ExperimentEvidenceClaim],
        signals_by_id: dict[uuid.UUID, MeasurementContractRequiredSignal],
        entries_by_id: dict[uuid.UUID, MetricEntry],
        value_by_key: dict[tuple[uuid.UUID, str], MetricValue],
        later_grouping_by_entry: dict[uuid.UUID, bool],
        contract: MeasurementContractVersion,
        started_at,
        is_structured: bool,
    ) -> list[_DatumWorking]:
        working: list[_DatumWorking] = []
        for claim in claims:
            # Step 1: structural binding, defensively re-verified (PEMD-DFR-OBS-1
            # leaves it service-only; never trusted purely from claim-creation time).
            signal = signals_by_id.get(claim.required_signal_id)
            entry = entries_by_id.get(claim.metric_entry_id)
            if signal is None or entry is None:  # pragma: no cover - FK guarantees existence
                continue

            item = _DatumWorking(
                claim=claim,
                entry=entry,
                value=value_by_key.get((claim.metric_entry_id, claim.metric_name)),
                signal=signal,
                channel=entry.channel,
                recorded_before_declaration=entry.created_at <= contract.created_at,
                later_grouping_entry_exists=later_grouping_by_entry.get(entry.id, False),
            )

            # Step 2: temporal classification (structured only — Legacy has none).
            if is_structured:
                if contract.declaration_level == _DECLARATION_LEVEL_DESCRIPTIVE:
                    outcome = classify_descriptive(
                        started_at=started_at,
                        window_days=contract.measurement_window_days,
                        period_start=entry.period_start,
                        period_end=entry.period_end,
                    )
                    if outcome == QUALIFYING:
                        item.temporal_role, item.usage_decision = "OBSERVATION", "CONSUMED"
                    elif outcome == OUT_OF_WINDOW:
                        item.usage_decision = "EXCLUDED_OUT_OF_WINDOW"
                    else:
                        assert outcome == EXCLUDED_AMBIGUOUS
                        item.usage_decision = "EXCLUDED_AMBIGUOUS"
                else:
                    outcome = classify_comparative(
                        started_at=started_at,
                        baseline_days=contract.baseline_window_days,
                        window_days=contract.measurement_window_days,
                        period_start=entry.period_start,
                        period_end=entry.period_end,
                    )
                    if outcome == QUALIFYING_BASELINE:
                        item.temporal_role, item.usage_decision = "BASELINE", "CONSUMED"
                    elif outcome == QUALIFYING_OBSERVATION:
                        item.temporal_role, item.usage_decision = "OBSERVATION", "CONSUMED"
                    elif outcome == OUT_OF_WINDOW:
                        item.usage_decision = "EXCLUDED_OUT_OF_WINDOW"
                    else:
                        assert outcome == EXCLUDED_AMBIGUOUS
                        item.usage_decision = "EXCLUDED_AMBIGUOUS"
            # else: LEGACY — no temporal qualification exists; item stays
            # temporal_role=None, usage_decision="CONSUMED" (its default).

            working.append(item)
        return working

    def _apply_multi_signal_exclusion(self, working: list[_DatumWorking]) -> None:
        """Step 3: same (metric_entry_id, metric_name) actively claimed under
        more than one RequiredSignal within this Start -> excluded on every
        conflicting side, disclosed, never selected. Applies uniformly to
        Legacy and structured alike (Reconciliation §L/AC)."""
        groups: dict[tuple[uuid.UUID, str], list[_DatumWorking]] = {}
        for item in working:
            if item.usage_decision != "CONSUMED":
                continue
            key = (item.claim.metric_entry_id, item.claim.metric_name)
            groups.setdefault(key, []).append(item)
        for items in groups.values():
            if len({item.signal.id for item in items}) > 1:
                for item in items:
                    item.usage_decision = "EXCLUDED_MULTI_SIGNAL"
                    # temporal_role is preserved (frozen matrix, §K/§17).

    def _apply_overlap_exclusion(self, working: list[_DatumWorking]) -> None:
        """Step 4 (structured only — Legacy has no window/anchor to detect
        overlap against): same signal + channel + temporal role, otherwise
        still qualifying, overlapping inclusive period ranges -> excluded on
        every conflicting side."""
        groups: dict[tuple[uuid.UUID, str, str | None], list[_DatumWorking]] = {}
        for item in working:
            if item.usage_decision != "CONSUMED":
                continue
            key = (item.signal.id, item.channel, item.temporal_role)
            groups.setdefault(key, []).append(item)
        for items in groups.values():
            if len(items) < 2:
                continue
            conflicted: set[int] = set()
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a, b = items[i].entry, items[j].entry
                    if periods_overlap(a.period_start, a.period_end, b.period_start, b.period_end):
                        conflicted.add(i)
                        conflicted.add(j)
            for index in conflicted:
                items[index].usage_decision = "EXCLUDED_CONFLICT"

    # --- steps 5/6: channel slicing + coverage/pair computation --------------

    def _persist_structured_outputs(
        self,
        *,
        run: ExperimentMeasurementRun,
        signals: list[MeasurementContractRequiredSignal],
        declaration_level: str,
        working: list[_DatumWorking],
    ) -> None:
        by_signal: dict[uuid.UUID, list[_DatumWorking]] = {}
        for item in working:
            by_signal.setdefault(item.signal.id, []).append(item)

        for signal in signals:
            signal_output = self.runs.add_signal_output(
                run=run, required_signal_id=signal.id, declaration_level=declaration_level
            )
            items = by_signal.get(signal.id, [])

            if signal.channel_binding == _CHANNEL_BINDING_EXACT:
                # V: EXACT always synthesizes its declared channel, even with
                # zero claims.
                channels = [signal.bound_channel]
            else:
                # U/R1: ANY slices derive from every distinct channel
                # actually represented among this signal's Run-input claims
                # (qualifying or excluded) — never a hypothetical vocabulary.
                channels = sorted({item.channel for item in items})

            for channel in channels:
                channel_items = [item for item in items if item.channel == channel]
                self._persist_slice(
                    signal_output=signal_output,
                    signal=signal,
                    declaration_level=declaration_level,
                    channel=channel,
                    items=channel_items,
                )

    def _persist_slice(
        self,
        *,
        signal_output,
        signal: MeasurementContractRequiredSignal,
        declaration_level: str,
        channel: str,
        items: list[_DatumWorking],
    ) -> None:
        qualifying = [item for item in items if item.usage_decision == "CONSUMED"]
        ambiguous = sum(1 for item in items if item.usage_decision == "EXCLUDED_AMBIGUOUS")
        out_of_window = sum(1 for item in items if item.usage_decision == "EXCLUDED_OUT_OF_WINDOW")
        multi_signal = sum(1 for item in items if item.usage_decision == "EXCLUDED_MULTI_SIGNAL")
        conflict = sum(1 for item in items if item.usage_decision == "EXCLUDED_CONFLICT")

        if declaration_level == _DECLARATION_LEVEL_DESCRIPTIVE:
            qualifying_count = len(qualifying)
            required_count = signal.min_data_points
            coverage_state = "COVERED" if qualifying_count >= required_count else "NOT_COVERED"
            self.runs.add_slice_output(
                signal_output=signal_output,
                channel=channel,
                qualifying_count=qualifying_count,
                required_count=required_count,
                coverage_state=coverage_state,
                pairing_state=None,
                ambiguous_excluded_count=ambiguous,
                conflict_excluded_count=conflict,
                multi_signal_excluded_count=multi_signal,
                out_of_window_count=out_of_window,
                baseline_value=None,
                observation_value=None,
                signed_arithmetic_difference=None,
            )
            return

        # COMPARATIVE
        baseline_items = [item for item in qualifying if item.temporal_role == "BASELINE"]
        observation_items = [item for item in qualifying if item.temporal_role == "OBSERVATION"]
        qualifying_count = len(qualifying)

        baseline_value = observation_value = signed_arithmetic_difference = None
        if not baseline_items or not observation_items:
            pairing_state = "INCOMPLETE"
        elif len(baseline_items) > 1 or len(observation_items) > 1:
            pairing_state = "SURPLUS"
        else:
            baseline_item, observation_item = baseline_items[0], observation_items[0]
            baseline_len = inclusive_period_length(baseline_item.entry.period_start, baseline_item.entry.period_end)
            observation_len = inclusive_period_length(
                observation_item.entry.period_start, observation_item.entry.period_end
            )
            if baseline_len != observation_len:
                pairing_state = "LENGTH_MISMATCH"
            else:
                pairing_state = "PAIR"
                if baseline_item.value is not None and observation_item.value is not None:
                    baseline_value = baseline_item.value.value
                    observation_value = observation_item.value.value
                    signed_arithmetic_difference = observation_value - baseline_value

        self.runs.add_slice_output(
            signal_output=signal_output,
            channel=channel,
            qualifying_count=qualifying_count,
            required_count=None,
            coverage_state=None,
            pairing_state=pairing_state,
            ambiguous_excluded_count=ambiguous,
            conflict_excluded_count=conflict,
            multi_signal_excluded_count=multi_signal,
            out_of_window_count=out_of_window,
            baseline_value=baseline_value,
            observation_value=observation_value,
            signed_arithmetic_difference=signed_arithmetic_difference,
        )

    # --- resolution helpers (phase 1, ordinary default isolation) ------------

    def _resolve_experiment(self, *, campaign: Campaign, experiment_public_id: str) -> Experiment:
        experiment = self.experiments.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=experiment_public_id
        )
        if experiment is None or experiment.workspace_id != campaign.workspace_id:
            raise ForbiddenError()
        return experiment

    def _resolve_start(
        self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID, start_public_id: str
    ) -> tuple[ExecutionStartAttestation, ExecutionAuthorization]:
        resolved = self.starts.get_with_authorization_by_public_id(
            experiment_id=experiment_id, workspace_id=workspace_id, public_id=start_public_id
        )
        if resolved is None:
            raise ForbiddenError()
        return resolved
