"""Governed Execution Start — the single writer of ``ExecutionStartAttestation``
(frozen Governed Execution Start Design Freeze).

CAPABILITY: an active workspace member ATTESTS that execution of ONE specific,
ACTIVE ``ExecutionAuthorization`` began at an attested instant. This is human
attestation only — the Core OS cannot observe external execution and no
machine ingestion exists. EXECUTION START ATTESTATION != VERIFIED EXTERNAL
EXECUTION != ASSIGNMENT != DELIVERY != DISTRIBUTION != EXPOSURE != EVIDENCE !=
MEASUREMENT != RESULT != WINNER != HYPOTHESIS VERDICT != VALIDITY !=
CAUSALITY. Nothing here writes any table other than
``execution_start_attestations`` and ``audit_events``.

Write order (frozen §8): resolve the campaign-scoped Experiment and its
Authorization -> fast-path replay lookup by ``client_request_id`` -> lock the
Experiment row -> lock the Authorization row ``FOR UPDATE`` -> re-check replay
under the lock -> already-started -> Authorization ACTIVE -> configuration
still current -> temporal validation -> insert + one audit event -> commit.

Lock order (frozen §21, S1): Experiment row, then the Authorization row. NO
Strategy lock and NO Strategy currency check — a valid active Authorization
may be started after a later Strategy revision (EXSTART-DF-OBS-3, accepted
debt). The existing global order (Strategy < Experiment < Authorization row)
is preserved; ``revise_strategy`` shares no lock with this writer. The audit
event deliberately carries NO ``strategy_id``/``hypothesis_id``: an audit FK
to the Strategy row takes an implicit KEY SHARE lock on it, which would invert
the canonical order while the Experiment lock is held (EXSTART-IMPL-OBS-1).

Temporal integrity (frozen §5, EXSTART-DF-OBS-2): ``started_at >=
authorization.created_at`` (strict, no tolerance) and ``started_at <= now +
5 minutes``. Both are SERVICE-LEVEL only — the first is cross-row and the
second is clock-relative, so no DB CHECK is claimed. Naive datetimes are
rejected by the request schema. ``started_at`` is never clamped.

Idempotency (frozen §8): ``UNIQUE(workspace_id, client_request_id)``; material
equality is exactly (Authorization, ``started_at``). A matching replay returns
the original record (200) even after the Authorization has been revoked and
creates nothing. Only the two known unique violations are translated; any
other ``IntegrityError`` re-raises.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.core.api_errors import (
    ExecutionStartAlreadyStartedError,
    ExecutionStartAuthorizationNotActiveError,
    ExecutionStartAuthorizationStaleError,
    ExecutionStartTimeInvalidError,
    ForbiddenError,
    IdempotencyKeyConflictError,
)
from app.strategy.models import ExecutionAuthorization, ExecutionStartAttestation, Experiment
from app.strategy.repository import (
    ExecutionAuthorizationRepository,
    ExecutionStartAttestationRepository,
    ExperimentDefinitionRepository,
    ExperimentRepository,
    ExperimentVariantRepository,
    MeasurementContractRepository,
)

EVENT_EXECUTION_START_ATTESTED = "strategy.execution_start.attested"

# Frozen §5 (EXSTART-DF-OBS-2): a Design Freeze parameter covering ordinary
# client clock skew — deliberately small, never a floor tolerance.
EXECUTION_START_FUTURE_TOLERANCE = timedelta(minutes=5)

_UQ_AUTHORIZATION_ID = "uq_execution_start_attestations_authorization_id"
_UQ_CLIENT_REQUEST_ID = "uq_execution_start_attestations_workspace_client_request_id"


def _server_now() -> datetime:
    """The single, testable server-clock read (one per request)."""
    return datetime.now(timezone.utc)


class ExperimentExecutionStartService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.experiments = ExperimentRepository(session)
        self.definitions = ExperimentDefinitionRepository(session)
        self.contracts = MeasurementContractRepository(session)
        self.variants = ExperimentVariantRepository(session)
        self.authorizations = ExecutionAuthorizationRepository(session)
        self.starts = ExecutionStartAttestationRepository(session)
        self.events = AuditEventRepository(session)

    # --- write (the single writer) ----------------------------------------

    def start(
        self,
        *,
        campaign: Campaign,
        experiment_public_id: str,
        authorization_public_id: str,
        client_request_id: str,
        started_at: datetime,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> tuple[ExecutionAuthorization, ExecutionStartAttestation, bool]:
        """Returns ``(authorization, start, created)``; ``created`` is False
        for a matching replay. ``actor_user_id`` is required and the audit
        actor is always ``ActorType.USER`` — no SYSTEM/AGENT path exists."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        # Plain identifiers, captured before any rollback can expire the ORM objects.
        experiment_id = experiment.id
        workspace_id = campaign.workspace_id
        campaign_id = campaign.id

        authorization = self.authorizations.get_by_public_id_for_experiment(
            experiment_id=experiment_id, workspace_id=workspace_id, public_id=authorization_public_id
        )
        if authorization is None:  # unknown / foreign / other Experiment: non-leaky
            raise ForbiddenError()
        authorization_id = authorization.id

        replay = self._replay(
            workspace_id=workspace_id,
            authorization_id=authorization_id,
            started_at=started_at,
            client_request_id=client_request_id,
        )
        if replay is not None:
            return authorization, replay, False

        # Lock order: Experiment row, then the Authorization row. No Strategy lock (S1).
        locked_experiment = self.experiments.get_by_id(experiment_id, for_update=True)
        if locked_experiment is None:  # pragma: no cover - FK guarantees existence
            raise ForbiddenError()
        authorization = self.authorizations.get_by_public_id_for_experiment(
            experiment_id=experiment_id, workspace_id=workspace_id, public_id=authorization_public_id, for_update=True
        )
        if authorization is None:  # pragma: no cover - resolved above; rows are never deleted
            raise ForbiddenError()

        replay = self._replay(
            workspace_id=workspace_id,
            authorization_id=authorization_id,
            started_at=started_at,
            client_request_id=client_request_id,
        )
        if replay is not None:
            return authorization, replay, False

        if self.starts.exists_for_authorization(authorization_id=authorization_id):
            raise ExecutionStartAlreadyStartedError()
        if authorization.revoked_at is not None:
            raise ExecutionStartAuthorizationNotActiveError()
        self._require_configuration_current(authorization=authorization)
        self._require_valid_start_time(authorization=authorization, started_at=started_at)

        authorization_public_id_value = authorization.public_id
        try:
            row = self.starts.create(
                workspace_id=workspace_id,
                authorization_id=authorization_id,
                started_at=started_at,
                client_request_id=client_request_id,
            )
            self.events.record(
                workspace_id=workspace_id,
                event_type=EVENT_EXECUTION_START_ATTESTED,
                actor_type=ActorType.USER,
                campaign_id=campaign_id,
                # NO strategy_id / hypothesis_id: an audit FK to the Strategy row
                # would implicitly take a KEY SHARE lock on it while this writer
                # already holds the Experiment lock — inverting the canonical
                # Strategy -> Experiment order used by authorize/Variant/Contract
                # and deadlocking against them (EXSTART-IMPL-OBS-1, found by the
                # forced-overlap tests). Revoke omits them for the same reason;
                # both are derivable Experiment -> Hypothesis -> Strategy.
                experiment_id=experiment_id,
                execution_authorization_id=authorization_id,
                execution_start_attestation_id=row.id,
                previous_state=None,
                new_state=f"attested:{authorization_public_id_value}",
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint in (_UQ_CLIENT_REQUEST_ID, _UQ_AUTHORIZATION_ID):
                # Either known backstop fired (the lock protocol normally makes both unreachable). A
                # same-key, same-material retry is a replay even when PostgreSQL reports the
                # authorization-id constraint first; anything else on that constraint is ALREADY_STARTED.
                replay = self._replay(
                    workspace_id=workspace_id,
                    authorization_id=authorization_id,
                    started_at=started_at,
                    client_request_id=client_request_id,
                )
                if replay is not None:
                    return self._reload_authorization(authorization_id), replay, False
                if constraint == _UQ_AUTHORIZATION_ID:
                    raise ExecutionStartAlreadyStartedError() from exc
            raise
        return authorization, row, True

    # --- internals -----------------------------------------------------------

    def _resolve_experiment(self, *, campaign: Campaign, experiment_public_id: str) -> Experiment:
        experiment = self.experiments.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=experiment_public_id
        )
        if experiment is None or experiment.workspace_id != campaign.workspace_id:
            raise ForbiddenError()
        return experiment

    def _reload_authorization(self, authorization_id: uuid.UUID) -> ExecutionAuthorization:
        row = self.session.get(ExecutionAuthorization, authorization_id)
        if row is None:  # pragma: no cover - rows are never deleted
            raise ForbiddenError()
        return row

    def _replay(
        self,
        *,
        workspace_id: uuid.UUID,
        authorization_id: uuid.UUID,
        started_at: datetime,
        client_request_id: str,
    ) -> ExecutionStartAttestation | None:
        """None when the key is unused; the ORIGINAL record when the key was
        used for the same Authorization and the same attested instant;
        otherwise a key conflict. Material is exactly (Authorization,
        ``started_at``) — nothing is re-derived, so a replay never
        re-validates time/active/configuration (retry != a new fact)."""
        existing = self.starts.get_by_workspace_and_request_id(
            workspace_id=workspace_id, client_request_id=client_request_id
        )
        if existing is None:
            return None
        if existing.authorization_id != authorization_id or existing.started_at != started_at:
            raise IdempotencyKeyConflictError()
        return existing

    def _require_configuration_current(self, *, authorization: ExecutionAuthorization) -> None:
        """Frozen §17/§18: under the Experiment lock, the Authorization must
        still represent the CURRENT executable configuration — same
        Definition tip, same Contract tip and a Variant snapshot equal to the
        COMPLETE live Variant set. The Authorization snapshot is never
        silently refreshed and no Variant row is copied."""
        experiment_id = authorization.experiment_id
        workspace_id = authorization.workspace_id
        definition_tip = self.definitions.get_tip(experiment_id=experiment_id, workspace_id=workspace_id)
        contract_tip = self.contracts.get_tip(experiment_id=experiment_id, workspace_id=workspace_id)
        if (
            definition_tip is None
            or contract_tip is None
            or authorization.definition_version_id != definition_tip.id
            or authorization.contract_version_id != contract_tip.id
        ):
            raise ExecutionStartAuthorizationStaleError()
        snapshot_ids = {
            row.variant_id
            for row in self.authorizations.list_variants_for_authorization(authorization_id=authorization.id)
        }
        live_ids = {
            variant.id
            for variant in self.variants.list_for_definition_version(definition_version_id=definition_tip.id)
        }
        if snapshot_ids != live_ids:
            raise ExecutionStartAuthorizationStaleError()

    def _require_valid_start_time(self, *, authorization: ExecutionAuthorization, started_at: datetime) -> None:
        """Strict floor (no tolerance) and a 5-minute future ceiling. One
        server-clock read; ``started_at`` is never clamped."""
        if started_at < authorization.created_at:
            raise ExecutionStartTimeInvalidError(
                "The attested start time cannot precede the creation of the execution authorization."
            )
        if started_at > _server_now() + EXECUTION_START_FUTURE_TOLERANCE:
            raise ExecutionStartTimeInvalidError("The attested start time cannot be in the future.")
