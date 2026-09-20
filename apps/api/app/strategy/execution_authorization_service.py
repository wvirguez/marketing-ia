"""Governed Execution Authorization — MVP-40 (frozen Execution Authorization
Discovery/Design Freeze).

``ExecutionAuthorizationService.authorize`` is the SINGLE writer of
``execution_authorizations``/``execution_authorization_variants``. An
Execution Authorization record asserts exactly one governed fact: THIS
SPECIFIC, immutable configuration (Experiment + the exact
``ExperimentDefinitionVersion`` tip + the complete ``ExperimentVariant`` set
existing under that tip + the exact ``MeasurementContractVersion`` tip, plus
an embedded, declared-only Execution Configuration) HAS BEEN AUTHORIZED TO
BEGIN FUTURE EXECUTION.

EXECUTION AUTHORIZATION != EXECUTION != ASSIGNMENT != ALLOCATION != EXPOSURE
!= EVIDENCE BINDING != TRACKING IMPLEMENTATION != TRACKING VALIDATION !=
EXPERIMENT RESULT != WINNER != HYPOTHESIS VERDICT != EXPERIMENTAL VALIDITY
!= CAUSALITY. Nothing here writes any table other than
``execution_authorizations``, ``execution_authorization_variants`` and
``audit_events``, allocates a unit, publishes/distributes content, creates
evidence, runs analysis, or links to Result/Winner/Learning/
CommercialOutcome.

Authorization subject (frozen Design Freeze §B/§C/§O/§P): the client never
supplies ``definition_version_id``/``contract_version_id``/Variant ids —
Authorization always pins whatever is CURRENT at the moment its Experiment
row lock is held: the current Measurement Contract tip (which, once
declared, structurally guarantees a permanently-pinned Definition tip — see
``ExperimentDefinitionService._has_pinning_children``), and the COMPLETE
Variant set existing under that Definition tip (no client-selected subset,
frozen §7). No Strategy reference is persisted (frozen §C, model "S1") —
Strategy currency is a create-time gate only, re-derived from
``Hypothesis.strategy_id``, mirroring every other writer in this chain.

Write order (frozen Design Freeze §Q/§21/§22/§23): resolve the
campaign-scoped Experiment -> resolve current material (Contract tip,
Definition, complete Variant set) WITHOUT a lock (fast-path read) -> fast-
path replay lookup against that material -> lock the Strategy row -> lock
the Experiment row (canonical order: Strategy, then Experiment -- the same
order every other writer in this chain uses) -> re-resolve current material
under the lock (never trust the pre-lock read for the actual write) ->
re-check replay under the lock -> the Strategy must still be current ->
check for an existing ACTIVE Authorization for this Experiment (for
auto-supersession, frozen §M/§17) -> insert the new Authorization row +
complete Variant-set snapshot as one atomic unit -> if a previous active
Authorization existed, mark it revoked/superseded in the SAME transaction
-> audit event(s) -> commit.

Single active Authorization per Experiment (frozen §M/§16): a NEW valid
Authorization request, when one is already active, automatically and
atomically supersedes it -- never two committed active rows, backstopped by
the partial unique index ``uq_execution_authorizations_experiment_active``.

Contract freeze (frozen §O/§13, MVP39B-OBS-2 resolution): NOT implemented
here as a field on this table. The freeze itself lives entirely in
``ExperimentMeasurementContractService.declare_or_revise``, which refuses a
revision while an ACTIVE Authorization pins the current Contract tip
(``exists_active_for_contract_version``) -- see that module for the
matching seam.

Revocation (frozen §L/§19): one-way, append-only, MEMBER+, reason required.
Recovery is always a brand-new Authorization -- no Variant mutation exists
anywhere (MVP38A-OBS-3 preserved, not resolved by mutating Variant).

Only the one known unique violation on ``execution_authorizations`` is
translated (``workspace_id``+``client_request_id`` -> rollback, re-read,
replay or ``IdempotencyKeyConflictError``). Any other ``IntegrityError`` --
including the partial-unique-index race, which the Experiment row lock
makes practically impossible -- re-raises: it is an invariant failure,
never a normal domain result.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.core.api_errors import (
    ExecutionAuthorizationInsufficientVariantsError,
    ExecutionAuthorizationNoMeasurementContractError,
    ExecutionAuthorizationNoneActiveError,
    ExecutionAuthorizationStrategyStaleError,
    ForbiddenError,
    IdempotencyKeyConflictError,
)
from app.strategy.models import (
    ComparisonType,
    Experiment,
    ExecutionAuthorization,
    ExecutionAuthorizationVariant,
    ExperimentDefinitionVersion,
    ExperimentVariant,
    MeasurementContractVersion,
)
from app.strategy.repository import (
    ExecutionAuthorizationRepository,
    ExperimentDefinitionRepository,
    ExperimentRepository,
    ExperimentVariantRepository,
    HypothesisRepository,
    MeasurementContractRepository,
    StrategyRepository,
)

EVENT_EXECUTION_AUTHORIZATION_AUTHORIZED = "strategy.execution_authorization.authorized"
EVENT_EXECUTION_AUTHORIZATION_REVOKED = "strategy.execution_authorization.revoked"

_UQ_CLIENT_REQUEST_ID = "uq_execution_authorizations_workspace_client_request_id"

_CurrentMaterial = tuple[MeasurementContractVersion, ExperimentDefinitionVersion, list[ExperimentVariant]]


class ExperimentExecutionAuthorizationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.strategies = StrategyRepository(session)
        self.hypotheses = HypothesisRepository(session)
        self.experiments = ExperimentRepository(session)
        self.definitions = ExperimentDefinitionRepository(session)
        self.contracts = MeasurementContractRepository(session)
        self.variants = ExperimentVariantRepository(session)
        self.authorizations = ExecutionAuthorizationRepository(session)
        self.events = AuditEventRepository(session)

    # --- reads -----------------------------------------------------------

    def get_current(
        self, *, campaign: Campaign, experiment_public_id: str
    ) -> tuple[Experiment, ExecutionAuthorization | None, list[ExecutionAuthorizationVariant]]:
        """The current ACTIVE Authorization, or ``None`` if none is active
        (never had one, or the most recent one was revoked). Readable for
        ANY Experiment of the campaign, including one under a superseded
        Strategy."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        active = self.authorizations.get_active_for_experiment(
            experiment_id=experiment.id, workspace_id=campaign.workspace_id
        )
        snapshot = self.authorizations.list_variants_for_authorization(authorization_id=active.id) if active else []
        return experiment, active, snapshot

    def get_history(
        self, *, campaign: Campaign, experiment_public_id: str
    ) -> tuple[Experiment, list[tuple[ExecutionAuthorization, list[ExecutionAuthorizationVariant]]]]:
        """Ascending, unpaginated history -- every Authorization ever
        created for this Experiment, active and revoked alike."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        rows = self.authorizations.list_for_experiment(
            experiment_id=experiment.id, workspace_id=campaign.workspace_id
        )
        return experiment, [
            (row, self.authorizations.list_variants_for_authorization(authorization_id=row.id)) for row in rows
        ]

    # --- write (the single writer) ----------------------------------------

    def authorize(
        self,
        *,
        campaign: Campaign,
        experiment_public_id: str,
        client_request_id: str,
        unit_of_assignment: str,
        allocation_design: str,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> tuple[ExecutionAuthorization, list[ExecutionAuthorizationVariant], bool]:
        """Returns ``(authorization, variant_snapshot, created)``; ``created``
        is False for a matching replay. Raises ``IdempotencyKeyConflictError``
        when the key was used for a materially different request (including
        one whose server-derived configuration has since changed)."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        experiment_id = experiment.id
        workspace_id = campaign.workspace_id
        campaign_id = campaign.id
        hypothesis_id = experiment.hypothesis_id

        material = self._resolve_current_material(experiment_id=experiment_id, workspace_id=workspace_id)
        replay = self._replay(
            workspace_id=workspace_id,
            experiment_id=experiment_id,
            material=material,
            unit_of_assignment=unit_of_assignment,
            allocation_design=allocation_design,
            client_request_id=client_request_id,
        )
        if replay is not None:
            row, snapshot_rows = replay
            return row, snapshot_rows, False

        hypothesis = self.hypotheses.get_by_id(hypothesis_id)
        if hypothesis is None:  # pragma: no cover - FK guarantees existence
            raise ForbiddenError()
        strategy_id = hypothesis.strategy_id

        # Canonical lock order: Strategy row, then Experiment row.
        strategy = self.strategies.get_by_id(strategy_id, for_update=True)
        if strategy is None:  # pragma: no cover - FK guarantees existence
            raise ForbiddenError()
        locked_experiment = self.experiments.get_by_id(experiment_id, for_update=True)
        if locked_experiment is None:  # pragma: no cover - FK guarantees existence
            raise ForbiddenError()

        # Never trust the pre-lock read for the actual write: re-resolve.
        material = self._resolve_current_material(experiment_id=experiment_id, workspace_id=workspace_id)
        replay = self._replay(
            workspace_id=workspace_id,
            experiment_id=experiment_id,
            material=material,
            unit_of_assignment=unit_of_assignment,
            allocation_design=allocation_design,
            client_request_id=client_request_id,
        )
        if replay is not None:
            row, snapshot_rows = replay
            return row, snapshot_rows, False

        current_strategy = self.strategies.get_current_for_campaign(campaign_id)
        if current_strategy is None or current_strategy.id != strategy_id:
            raise ExecutionAuthorizationStrategyStaleError()

        contract_tip, definition, variant_rows = material
        variant_ids = [variant.id for variant in variant_rows]

        previous_active = self.authorizations.get_active_for_experiment(
            experiment_id=experiment_id, workspace_id=workspace_id, for_update=True
        )

        try:
            # The partial unique index (uq_execution_authorizations_experiment_active,
            # WHERE revoked_at IS NULL) requires the predecessor's revoked_at to be
            # flushed BEFORE the new row is inserted — otherwise both rows are
            # simultaneously "active" for one statement and the insert violates it.
            if previous_active is not None:
                previous_active.revoked_at = datetime.now(timezone.utc)
                previous_active.revoked_reason = "superseded by re-authorization"
                self.session.flush()

            new_row, snapshot_rows = self.authorizations.create(
                experiment_id=experiment_id,
                workspace_id=workspace_id,
                definition_version_id=definition.id,
                contract_version_id=contract_tip.id,
                variant_ids=variant_ids,
                unit_of_assignment=unit_of_assignment,
                allocation_design=allocation_design,
                client_request_id=client_request_id,
            )

            if previous_active is not None:
                previous_active.superseded_by_execution_authorization_id = new_row.id
                self.events.record(
                    workspace_id=workspace_id,
                    event_type=EVENT_EXECUTION_AUTHORIZATION_REVOKED,
                    actor_type=ActorType.USER,
                    campaign_id=campaign_id,
                    strategy_id=strategy_id,
                    hypothesis_id=hypothesis_id,
                    experiment_id=experiment_id,
                    execution_authorization_id=previous_active.id,
                    previous_state="ACTIVE",
                    new_state=f"superseded_by:{new_row.public_id}",
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )

            self.events.record(
                workspace_id=workspace_id,
                event_type=EVENT_EXECUTION_AUTHORIZATION_AUTHORIZED,
                actor_type=ActorType.USER,
                campaign_id=campaign_id,
                strategy_id=strategy_id,
                hypothesis_id=hypothesis_id,
                experiment_id=experiment_id,
                experiment_definition_version_id=definition.id,
                measurement_contract_id=contract_tip.id,
                execution_authorization_id=new_row.id,
                previous_state=(f"supersedes:{previous_active.public_id}" if previous_active is not None else None),
                new_state="ACTIVE",
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == _UQ_CLIENT_REQUEST_ID:
                material = self._resolve_current_material(experiment_id=experiment_id, workspace_id=workspace_id)
                replay = self._replay(
                    workspace_id=workspace_id,
                    experiment_id=experiment_id,
                    material=material,
                    unit_of_assignment=unit_of_assignment,
                    allocation_design=allocation_design,
                    client_request_id=client_request_id,
                )
                if replay is not None:
                    replay_row, replay_signals = replay
                    return replay_row, replay_signals, False
            raise
        return new_row, snapshot_rows, True

    def revoke(
        self,
        *,
        campaign: Campaign,
        experiment_public_id: str,
        reason: str,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> ExecutionAuthorization:
        """Revokes the current ACTIVE Authorization for this Experiment, if
        any -- one-way, non-reversible (frozen §L/§19). No Strategy lock is
        needed (revocation never checks Strategy currency); only the
        Experiment row lock, which every writer that could race with this
        one already acquires."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        experiment_id = experiment.id
        workspace_id = campaign.workspace_id

        locked_experiment = self.experiments.get_by_id(experiment_id, for_update=True)
        if locked_experiment is None:  # pragma: no cover - already resolved above
            raise ForbiddenError()

        active = self.authorizations.get_active_for_experiment(
            experiment_id=experiment_id, workspace_id=workspace_id, for_update=True
        )
        if active is None:
            raise ExecutionAuthorizationNoneActiveError()

        active.revoked_at = datetime.now(timezone.utc)
        active.revoked_reason = reason
        self.events.record(
            workspace_id=workspace_id,
            event_type=EVENT_EXECUTION_AUTHORIZATION_REVOKED,
            actor_type=ActorType.USER,
            campaign_id=campaign.id,
            experiment_id=experiment_id,
            execution_authorization_id=active.id,
            previous_state="ACTIVE",
            new_state="REVOKED",
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()
        return active

    # --- internals -----------------------------------------------------------

    def _resolve_experiment(self, *, campaign: Campaign, experiment_public_id: str) -> Experiment:
        experiment = self.experiments.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=experiment_public_id
        )
        if experiment is None or experiment.workspace_id != campaign.workspace_id:
            raise ForbiddenError()
        return experiment

    def _resolve_current_material(self, *, experiment_id: uuid.UUID, workspace_id: uuid.UUID) -> _CurrentMaterial:
        """The three HARD prerequisites (frozen §P/§I/§11): a Measurement
        Contract tip must exist, its pinned Definition must exist, and the
        complete Variant set under that Definition must meet the
        comparison-type-dependent minimum cardinality."""
        contract_tip = self.contracts.get_tip(experiment_id=experiment_id, workspace_id=workspace_id)
        if contract_tip is None:
            raise ExecutionAuthorizationNoMeasurementContractError()
        definition = self.definitions.get_by_id(contract_tip.definition_version_id)
        if definition is None:  # pragma: no cover - FK guarantees existence
            raise ForbiddenError()
        variant_rows = self.variants.list_for_definition_version(definition_version_id=definition.id)
        minimum = 2 if definition.comparison_type == ComparisonType.CONTROLLED.value else 1
        if len(variant_rows) < minimum:
            raise ExecutionAuthorizationInsufficientVariantsError()
        return contract_tip, definition, variant_rows

    def _replay(
        self,
        *,
        workspace_id: uuid.UUID,
        experiment_id: uuid.UUID,
        material: _CurrentMaterial,
        unit_of_assignment: str,
        allocation_design: str,
        client_request_id: str,
    ) -> tuple[ExecutionAuthorization, list[ExecutionAuthorizationVariant]] | None:
        """None when the key is unused; ``(row, snapshot)`` when the key was
        used for a materially equal request -- same Experiment, same
        currently-resolvable Definition/Contract/complete-Variant-set, same
        Execution Configuration; otherwise a key conflict. Material equality
        intentionally re-derives the server-side fields from CURRENT state
        (frozen Design Freeze §AJ) -- a replay of an old key against
        since-changed Contract/Definition/Variant state is a conflict, not a
        silent stale success."""
        existing = self.authorizations.get_by_workspace_and_request_id(
            workspace_id=workspace_id, client_request_id=client_request_id
        )
        if existing is None:
            return None
        if existing.experiment_id != experiment_id:
            raise IdempotencyKeyConflictError()

        contract_tip, definition, variant_rows = material
        existing_snapshot = self.authorizations.list_variants_for_authorization(authorization_id=existing.id)
        existing_variant_ids = {row.variant_id for row in existing_snapshot}
        current_variant_ids = {variant.id for variant in variant_rows}

        if (
            existing.definition_version_id == definition.id
            and existing.contract_version_id == contract_tip.id
            and existing_variant_ids == current_variant_ids
            and existing.unit_of_assignment == unit_of_assignment
            and existing.allocation_design == allocation_design
        ):
            return existing, existing_snapshot
        raise IdempotencyKeyConflictError()
