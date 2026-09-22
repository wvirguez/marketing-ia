"""Experiment Evidence Binding — the single writer of ``ExperimentEvidenceClaim``
(frozen Experiment Evidence Binding Design Freeze).

CAPABILITY: an authenticated workspace member CLAIMS that one metric datum
``(MetricEntry, metric_name)`` is associated with one ``RequiredSignal`` under
ONE started execution attempt (the Start). It is a PROVENANCE CLAIM ONLY:
EVIDENCE CLAIM != ELIGIBILITY != TEMPORAL VALIDITY != CURRENTNESS !=
SUFFICIENCY != CORRECTNESS != TRACKING VALIDITY != ASSIGNMENT != EXPOSURE !=
VARIANT ATTRIBUTION != COMPARABILITY != MEASUREMENT != EXPERIMENT RESULT !=
WINNER != HYPOTHESIS VERDICT != EXPERIMENTAL VALIDITY != ATTRIBUTION !=
CAUSALITY != LEARNING VALIDITY. Nothing here writes any table other than
``experiment_evidence_claims`` and ``audit_events``.

Create write order (frozen §AF): resolve the campaign-scoped Experiment and
Start -> fast-path replay lookup by ``client_request_id`` (soft resolution, no
lock) -> lock the Experiment row -> re-check replay under the lock -> resolve
and validate the RequiredSignal / MetricEntry / metric name -> active-duplicate
check -> insert + one audit event -> commit. Dispose: resolve -> lock the
Experiment row -> lock the claim row ``FOR UPDATE`` -> already-disposed check
-> one-way update + one audit event -> commit.

Lock order (frozen §AF): Experiment row, then the claim row. NO Strategy lock,
NO Hypothesis lock and NO Authorization ``FOR UPDATE``. The Experiment lock is
required for LOCK ORDER, not for semantic validity (the Start, Authorization,
RequiredSignal, MetricEntry and MetricValue rows are all immutable): the
claim's FKs take an implicit KEY SHARE on the Authorization row, and revoke /
authorize / start hold the Experiment lock and then take ``FOR UPDATE`` on that
same Authorization — without the Experiment lock first, the claim's audit FK
(KEY SHARE on the Experiment) would wait on revoke while revoke waits on the
claim's KEY SHARE, a deadlock (the EXSTART-IMPL-OBS-1 class). The audit event
deliberately carries NO ``strategy_id``/``hypothesis_id``/``metric_entry_id``.

Firewalls (frozen): NO temporal validation (T1 — ``started_at`` and the metric
period are never compared), NO Variant/Assignment/Exposure, ``tracking_required``
never blocks, revocation never blocks (R2 — late provenance claims are allowed
after the Authorization is revoked, and no current-Strategy or active
Authorization is required), a claim on an already non-current datum is allowed
(C1). This domain never decides which claim or datum a Measurement counts.

Idempotency (frozen §AE): ``UNIQUE(workspace_id, client_request_id)``; material
equality is exactly (Start, RequiredSignal, MetricEntry, ``metric_name``). A
matching replay returns the ORIGINAL claim in its CURRENT state (even if since
disposed, even after revocation) and creates nothing; nothing is re-validated.
Only the two known unique violations are translated, replay-aware; any other
``IntegrityError`` re-raises. Dispose carries no idempotency key (EEB-DF-OBS-2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.content.models import ContentDistribution
from app.core.api_errors import (
    EvidenceClaimAlreadyActiveError,
    EvidenceClaimAlreadyDisposedError,
    EvidenceClaimChannelNotBoundError,
    EvidenceClaimMetricNotBoundError,
    EvidenceClaimMetricNotInEntryError,
    EvidenceClaimSignalNotInPinnedContractError,
    ForbiddenError,
    IdempotencyKeyConflictError,
)
from app.measurement.models import DistributionMetricEvidence, MetricEntry
from app.measurement.repository import (
    DistributionMetricEvidenceRepository,
    MetricEntryRepository,
    MetricValueRepository,
)
from app.strategy.measurement_declaration import CHANNEL_BINDING_EXACT
from app.strategy.models import (
    ExecutionAuthorization,
    ExecutionStartAttestation,
    Experiment,
    ExperimentEvidenceClaim,
    MeasurementContractRequiredSignal,
    MeasurementContractVersion,
)
from app.strategy.repository import (
    ExecutionStartAttestationRepository,
    ExperimentEvidenceClaimRepository,
    ExperimentRepository,
    MeasurementContractRepository,
)
from app.users.repository import UserRepository

EVENT_EVIDENCE_CLAIM_CLAIMED = "strategy.evidence_claim.claimed"
EVENT_EVIDENCE_CLAIM_DISPOSED = "strategy.evidence_claim.disposed"

_UQ_CLIENT_REQUEST_ID = "uq_experiment_evidence_claims_workspace_client_request_id"
_UQ_ACTIVE_DATUM = "uq_experiment_evidence_claims_active_datum"


@dataclass(frozen=True)
class EvidenceClaimView:
    """Everything the read model needs for ONE claim, resolved through the
    datum/attempt FKs — nothing here is copied onto the claim row. The
    ``later_correction_exists`` observation is computed at read time, is never
    stored, and never gates anything."""

    claim: ExperimentEvidenceClaim
    start: ExecutionStartAttestation
    authorization: ExecutionAuthorization
    contract_version: MeasurementContractVersion | None
    signal: MeasurementContractRequiredSignal | None
    entry: MetricEntry | None
    value: Decimal | None
    evidence: DistributionMetricEvidence | None
    distribution: ContentDistribution | None
    superseded_by_evidence: DistributionMetricEvidence | None
    supersedes_evidence: DistributionMetricEvidence | None
    later_correction_exists: bool
    claimed_by_public_id: str | None
    disposed_by_public_id: str | None


class ExperimentEvidenceClaimService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.experiments = ExperimentRepository(session)
        self.starts = ExecutionStartAttestationRepository(session)
        self.contracts = MeasurementContractRepository(session)
        self.claims = ExperimentEvidenceClaimRepository(session)
        self.entries = MetricEntryRepository(session)
        self.values = MetricValueRepository(session)
        self.evidence = DistributionMetricEvidenceRepository(session)
        self.users = UserRepository(session)
        self.events = AuditEventRepository(session)

    # --- writes --------------------------------------------------------------

    def create(
        self,
        *,
        campaign: Campaign,
        experiment_public_id: str,
        start_public_id: str,
        client_request_id: str,
        required_signal_public_id: str,
        metric_entry_public_id: str,
        metric_name: str,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> tuple[ExperimentEvidenceClaim, bool]:
        """Returns ``(claim, created)``; ``created`` is False for a matching
        replay. ``actor_user_id`` is required and the audit actor is always
        ``ActorType.USER`` — no SYSTEM/AGENT path exists."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        # Plain identifiers, captured before any rollback can expire the ORM objects.
        experiment_id = experiment.id
        workspace_id = campaign.workspace_id
        campaign_id = campaign.id

        start, authorization = self._resolve_start(
            experiment_id=experiment_id, workspace_id=workspace_id, start_public_id=start_public_id
        )
        start_id = start.id
        authorization_id = authorization.id
        contract_version_id = authorization.contract_version_id

        # Soft resolution (no raise): only used to compare replay material.
        signal = self.contracts.get_signal_by_public_id_for_experiment(
            experiment_id=experiment_id, workspace_id=workspace_id, public_id=required_signal_public_id
        )
        entry = self._resolve_entry(campaign=campaign, metric_entry_public_id=metric_entry_public_id)
        signal_id = signal.id if signal is not None else None
        entry_id = entry.id if entry is not None else None

        replay = self._replay(
            workspace_id=workspace_id,
            start_id=start_id,
            signal_id=signal_id,
            entry_id=entry_id,
            metric_name=metric_name,
            client_request_id=client_request_id,
        )
        if replay is not None:
            return replay, False

        # Lock order: Experiment row only. No Strategy / Hypothesis / Authorization lock.
        locked_experiment = self.experiments.get_by_id(experiment_id, for_update=True)
        if locked_experiment is None:  # pragma: no cover - FK guarantees existence
            raise ForbiddenError()

        replay = self._replay(
            workspace_id=workspace_id,
            start_id=start_id,
            signal_id=signal_id,
            entry_id=entry_id,
            metric_name=metric_name,
            client_request_id=client_request_id,
        )
        if replay is not None:
            return replay, False

        # Strict resolution and validation. Non-leaky first (unknown / foreign
        # target), then the typed 422s.
        if signal_id is None or entry_id is None:
            raise ForbiddenError()
        if signal.contract_version_id != contract_version_id:
            raise EvidenceClaimSignalNotInPinnedContractError()
        if metric_name not in {value.metric_name for value in self.values.list_for_entry(entry_id)}:
            raise EvidenceClaimMetricNotInEntryError()
        self._require_declared_binding(
            contract_version_id=contract_version_id, signal=signal, entry=entry, metric_name=metric_name
        )
        if (
            self.claims.get_active_for_material(
                start_id=start_id, required_signal_id=signal_id, metric_entry_id=entry_id, metric_name=metric_name
            )
            is not None
        ):
            raise EvidenceClaimAlreadyActiveError()

        try:
            row = self.claims.create(
                workspace_id=workspace_id,
                start_id=start_id,
                authorization_id=authorization_id,
                experiment_id=experiment_id,
                contract_version_id=contract_version_id,
                required_signal_id=signal_id,
                metric_entry_id=entry_id,
                metric_name=metric_name,
                client_request_id=client_request_id,
                claimed_by_user_id=actor_user_id,
            )
            claim_public_id = row.public_id
            self.events.record(
                workspace_id=workspace_id,
                event_type=EVENT_EVIDENCE_CLAIM_CLAIMED,
                actor_type=ActorType.USER,
                campaign_id=campaign_id,
                # NO strategy_id / hypothesis_id / metric_entry_id: see the module
                # docstring (EXSTART-IMPL-OBS-1). Derivable through the claim row.
                experiment_id=experiment_id,
                execution_authorization_id=authorization_id,
                execution_start_attestation_id=start_id,
                experiment_evidence_claim_id=row.id,
                previous_state=None,
                new_state=f"claimed:{claim_public_id}",
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint in (_UQ_CLIENT_REQUEST_ID, _UQ_ACTIVE_DATUM):
                # Either known backstop fired (the lock protocol normally makes both
                # unreachable). A same-key, same-material retry is a replay even when
                # PostgreSQL reports the active-datum index first; anything else on
                # that index is a genuine active duplicate.
                replay = self._replay(
                    workspace_id=workspace_id,
                    start_id=start_id,
                    signal_id=signal_id,
                    entry_id=entry_id,
                    metric_name=metric_name,
                    client_request_id=client_request_id,
                )
                if replay is not None:
                    return replay, False
                if constraint == _UQ_ACTIVE_DATUM:
                    raise EvidenceClaimAlreadyActiveError() from exc
            raise
        return row, True

    def _require_declared_binding(
        self,
        *,
        contract_version_id: uuid.UUID,
        signal: MeasurementContractRequiredSignal,
        entry: MetricEntry,
        metric_name: str,
    ) -> None:
        """Pre-Execution Measurement Declaration: for a STRUCTURED pinned Contract ONLY, the
        claim must be structurally compatible with the signal's declared binding — the claimed
        metric name equals ``bound_metric_name`` and, for an EXACT binding, the entry's channel
        equals ``bound_channel`` (both exact and case-sensitive; ANY accepts any channel). A
        LEGACY Contract keeps the previous behavior unchanged. Compatible means ONLY structurally
        compatible: never eligible, valid, sufficient, current, correct or successful."""
        contract = self.contracts.get_by_id(contract_version_id)
        if contract is None or contract.declaration_level is None:
            return
        if signal.bound_metric_name != metric_name:
            raise EvidenceClaimMetricNotBoundError()
        if signal.channel_binding == CHANNEL_BINDING_EXACT and entry.channel != signal.bound_channel:
            raise EvidenceClaimChannelNotBoundError()

    def dispose(
        self,
        *,
        campaign: Campaign,
        experiment_public_id: str,
        start_public_id: str,
        claim_public_id: str,
        reason: str,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> ExperimentEvidenceClaim:
        """One-way disposal. Any active workspace member (MEMBER+) may dispose
        any claim of the workspace; the audit actor is always the USER."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        experiment_id = experiment.id
        workspace_id = campaign.workspace_id
        campaign_id = campaign.id

        start, authorization = self._resolve_start(
            experiment_id=experiment_id, workspace_id=workspace_id, start_public_id=start_public_id
        )
        start_id = start.id
        authorization_id = authorization.id
        if (
            self.claims.get_for_start_by_public_id(
                start_id=start_id, workspace_id=workspace_id, public_id=claim_public_id
            )
            is None
        ):  # unknown / foreign / other Start: non-leaky
            raise ForbiddenError()

        # Lock order: Experiment row, then the claim row.
        locked_experiment = self.experiments.get_by_id(experiment_id, for_update=True)
        if locked_experiment is None:  # pragma: no cover - FK guarantees existence
            raise ForbiddenError()
        claim = self.claims.get_for_start_by_public_id(
            start_id=start_id, workspace_id=workspace_id, public_id=claim_public_id, for_update=True
        )
        if claim is None:  # pragma: no cover - resolved above; rows are never deleted
            raise ForbiddenError()
        if claim.disposed_at is not None:
            raise EvidenceClaimAlreadyDisposedError()

        claim_id = claim.id
        claim_public_id_value = claim.public_id
        self.claims.dispose(
            claim, disposed_at=datetime.now(timezone.utc), disposed_by_user_id=actor_user_id, reason=reason
        )
        self.events.record(
            workspace_id=workspace_id,
            event_type=EVENT_EVIDENCE_CLAIM_DISPOSED,
            actor_type=ActorType.USER,
            campaign_id=campaign_id,
            experiment_id=experiment_id,
            execution_authorization_id=authorization_id,
            execution_start_attestation_id=start_id,
            experiment_evidence_claim_id=claim_id,
            previous_state=f"claimed:{claim_public_id_value}",
            new_state=f"disposed:{claim_public_id_value}",
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return claim

    # --- reads ---------------------------------------------------------------

    def list_for_start(
        self, *, campaign: Campaign, experiment_public_id: str, start_public_id: str
    ) -> tuple[Experiment, list[EvidenceClaimView]]:
        """Every claim of the Start — active AND disposed — ascending. Readable
        for any Experiment of the campaign, including one under a superseded
        Strategy. Takes no lock."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        start, authorization = self._resolve_start(
            experiment_id=experiment.id, workspace_id=campaign.workspace_id, start_public_id=start_public_id
        )
        claims = self.claims.list_for_start(start_id=start.id, workspace_id=campaign.workspace_id)
        return experiment, [self.view(claim, start=start, authorization=authorization) for claim in claims]

    def view_for_claim(self, claim: ExperimentEvidenceClaim) -> EvidenceClaimView:
        """The read model of one already-persisted claim (its Start and
        Authorization are loaded through the claim's own FKs)."""
        start = self.session.get(ExecutionStartAttestation, claim.start_id)
        authorization = self.session.get(ExecutionAuthorization, claim.authorization_id)
        if start is None or authorization is None:  # pragma: no cover - composite FKs guarantee both
            raise ForbiddenError()
        return self.view(claim, start=start, authorization=authorization)

    def view(
        self, claim: ExperimentEvidenceClaim, *, start: ExecutionStartAttestation, authorization: ExecutionAuthorization
    ) -> EvidenceClaimView:
        """Resolves the literal facts for one claim. Read-time observations
        only — never stored, never a gate, never an eligibility conclusion."""
        entry = self.entries.get_by_id(claim.metric_entry_id)
        value = next(
            (row.value for row in self.values.list_for_entry(claim.metric_entry_id) if row.metric_name == claim.metric_name),
            None,
        )
        evidence = self.evidence.get_by_metric_entry_id(claim.metric_entry_id)
        distribution = self.session.get(ContentDistribution, evidence.distribution_id) if evidence is not None else None
        superseded_by = self.evidence.get_successor(evidence.id) if evidence is not None else None
        supersedes = (
            self.evidence.get_by_id(evidence.supersedes_evidence_id)
            if evidence is not None and evidence.supersedes_evidence_id is not None
            else None
        )
        if evidence is not None:
            later_correction_exists = superseded_by is not None
        else:
            later_correction_exists = entry is not None and self.entries.exists_later_in_grouping(entry)
        return EvidenceClaimView(
            claim=claim,
            start=start,
            authorization=authorization,
            contract_version=self.contracts.get_by_id(claim.contract_version_id),
            signal=self.session.get(MeasurementContractRequiredSignal, claim.required_signal_id),
            entry=entry,
            value=value,
            evidence=evidence,
            distribution=distribution,
            superseded_by_evidence=superseded_by,
            supersedes_evidence=supersedes,
            later_correction_exists=later_correction_exists,
            claimed_by_public_id=self._user_public_id(claim.claimed_by_user_id),
            disposed_by_public_id=self._user_public_id(claim.disposed_by_user_id),
        )

    # --- internals -----------------------------------------------------------

    def _user_public_id(self, user_id: uuid.UUID | None) -> str | None:
        """Same convention as the measurement router's ``_reporter_public_id``:
        the user's public id, ``None`` when unresolved."""
        if user_id is None:
            return None
        user = self.users.get_by_id(user_id)
        return user.public_id if user is not None else None

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
        if resolved is None:  # unknown / foreign / other Experiment: non-leaky
            raise ForbiddenError()
        return resolved

    def _resolve_entry(self, *, campaign: Campaign, metric_entry_public_id: str) -> MetricEntry | None:
        """Campaign integrity is SERVICE-enforced (EEB-DF-OBS-1): the entry must
        belong to the route's campaign AND workspace. ``None`` for unknown /
        cross-campaign / cross-workspace — never distinguishable to the caller."""
        entry = self.entries.get_by_public_id(metric_entry_public_id)
        if entry is None or entry.campaign_id != campaign.id or entry.workspace_id != campaign.workspace_id:
            return None
        return entry

    def _replay(
        self,
        *,
        workspace_id: uuid.UUID,
        start_id: uuid.UUID,
        signal_id: uuid.UUID | None,
        entry_id: uuid.UUID | None,
        metric_name: str,
        client_request_id: str,
    ) -> ExperimentEvidenceClaim | None:
        """None when the key is unused; the ORIGINAL claim (in its CURRENT
        state) when the key was used for the same material; otherwise a key
        conflict. Material is exactly (Start, RequiredSignal, MetricEntry,
        ``metric_name``) — nothing is re-derived or re-validated, so a replay
        never re-checks revocation, disposal or the datum (retry != a new fact).
        An unresolvable signal/entry can never equal stored material."""
        existing = self.claims.get_by_workspace_and_request_id(
            workspace_id=workspace_id, client_request_id=client_request_id
        )
        if existing is None:
            return None
        if (
            signal_id is None
            or entry_id is None
            or existing.start_id != start_id
            or existing.required_signal_id != signal_id
            or existing.metric_entry_id != entry_id
            or existing.metric_name != metric_name
        ):
            raise IdempotencyKeyConflictError()
        return existing
