"""Governed Measurement Contract — MVP-39 (frozen MVP-39A/-39B).

``ExperimentMeasurementContractService.declare_or_revise`` is the SINGLE
writer of ``measurement_contract_versions``/``measurement_contract_signals``.
A Measurement Contract version is the immutable, append-only, PRE-EXECUTION
declaration of how one Experiment's evidence is intended to be evaluated,
pinned to ONE immutable ``ExperimentDefinitionVersion`` (the current tip at
declaration time — the same tip forever afterward, since the Definition
becomes permanently pinned the instant a first Contract version exists).

MEASUREMENT CONTRACT != EVIDENCE != EVIDENCE BINDING != TRACKING
IMPLEMENTATION != MEASUREMENT EXECUTION != ALLOCATION != EXPOSURE !=
EXECUTION AUTHORIZATION != EXPERIMENT RESULT != WINNER != HYPOTHESIS
VERDICT != LEARNING VALIDATION != ATTRIBUTION != CAUSALITY. Nothing here
writes any table other than ``measurement_contract_versions``,
``measurement_contract_signals`` and ``audit_events``, transitions any
status, or links to content, distribution, tracking, evidence or
commercial outcomes.

Write order (frozen MVP-39B §X/§Y/§Z/§28): resolve the campaign-scoped
Experiment → resolve the requested ``EXD-…`` INSIDE that Experiment (needed
for both a first declaration and every later revision, since it never
changes once pinned) → fast-path replay lookup → lock the Strategy row →
lock the Experiment row (canonical order: Strategy, then Experiment — the
same order ``ExperimentDefinitionService``/``ExperimentVariantService`` use,
so all three writers serialize on the Experiment row) → re-check replay
under the locks → the Strategy must still be current → ``base_version`` must
equal the Contract's own current tip → the requested Definition version must
equal the Definition's re-read tip (never silently rebased) → a CONTROLLED
comparison requires ``success_criterion`` → reject an unchanged revision →
insert (Contract row + every RequiredSignal row, one aggregate unit) + audit
→ commit.

Definition pinning (MVP-39B §D/§17): the first Contract version pins the
Definition immediately, via the SAME ``ExperimentDefinitionService.
_has_pinning_children`` seam ``ExperimentVariantService`` already
participates in — Contract and Variant are independent, equal-weight
siblings; this service never checks Variant's existence and vice versa.

Contract freeze (MVP-39B §E/§19; resolved MVP-40, frozen Design Freeze
§O/§P): a Contract revision is refused (``MeasurementContractFrozenByAuthorizationError``)
while an ACTIVE ``ExecutionAuthorization`` pins the current Contract tip —
checked here, under the same Experiment row lock, via
``ExecutionAuthorizationRepository.exists_active_for_contract_version``.
This is the ONLY place the freeze is enforced; ``ExecutionAuthorization``
itself carries no ``status``/``frozen_at`` field. The ACTIVE-Authorization
freeze lifts automatically the moment no active Authorization remains for
the Experiment — but only while NO Execution Start exists (EXAUTH-DF-OBS-1,
resolved by the Governed Execution Start design, model C1): once ANY
``ExecutionStartAttestation`` exists for the Experiment the Contract lineage
is PERMANENTLY frozen (``MeasurementContractFrozenByExecutionStartError``),
even after the Authorization is revoked. That check runs after the
base-version check and BEFORE the active-Authorization freeze, so the error
code is deterministic. Recovery after a start is a new Experiment lineage.

Pre-Execution Measurement Declaration (Model A + D, no new aggregate): a Contract
version may carry a STRUCTURED declaration (``declaration_level`` DESCRIPTIVE /
COMPARATIVE, semantics version 1, plus per-signal metric/channel bindings) of how a
future Measurement WOULD read evidence. It is validated here — completeness against
its level and binding-slot ownership are pure service rules
(``app.strategy.measurement_declaration``), a CONTROLLED Definition rejects it — and
every new field participates in ``_content_equal``, so replay, conflict and unchanged
detection cover it. It inherits the C1 freeze above (no second freeze mechanism) and
adds no lock participant, no route and no audit event: the existing declared/revised
events remain the lifecycle. DECLARATION != APPLICATION != MEASUREMENT != RESULT !=
VERDICT != LEARNING; a declaration is not pre-registration and validates nothing.

Only the known unique violations on ``measurement_contract_versions``
are translated (``experiment_id``+``version`` → ``MEASUREMENT_CONTRACT_
BASE_STALE``; ``workspace_id``+``client_request_id`` → rollback, re-read,
replay or ``IDEMPOTENCY_KEY_CONFLICT``) plus the binding-slot index on the signals
(``MEASUREMENT_CONTRACT_BINDING_CONFLICT``). Any other ``IntegrityError`` —
including a RequiredSignal ordinal/name collision, which the schema-level
duplicate check and the Experiment row lock both make practically
impossible — re-raises: it is an invariant failure, never a normal domain
result.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign
from app.core.api_errors import (
    MeasurementContractBindingConflictError,
    MeasurementContractControlledDeclarationNotSupportedError,
    MeasurementContractDeclarationInvalidError,
    MeasurementContractFrozenByExecutionStartError,
    ForbiddenError,
    IdempotencyKeyConflictError,
    MeasurementContractBaseStaleError,
    MeasurementContractDefinitionVersionNotCurrentError,
    MeasurementContractFrozenByAuthorizationError,
    MeasurementContractStrategyStaleError,
    MeasurementContractSuccessCriterionRequiredError,
    MeasurementContractUnchangedError,
)
from app.strategy.measurement_declaration import (
    BINDING_FIELDS,
    DECLARATION_CONTRACT_FIELDS,
    declaration_shape_problems,
    first_binding_ownership_conflict,
    first_duplicate_binding_slot,
)
from app.strategy.models import (
    ComparisonType,
    Experiment,
    ExperimentDefinitionVersion,
    MeasurementContractRequiredSignal,
    MeasurementContractVersion,
)
from app.strategy.repository import (
    ExecutionAuthorizationRepository,
    ExecutionStartAttestationRepository,
    ExperimentDefinitionRepository,
    ExperimentRepository,
    HypothesisRepository,
    MeasurementContractRepository,
    StrategyRepository,
)

EVENT_MEASUREMENT_CONTRACT_DECLARED = "strategy.measurement_contract.declared"
EVENT_MEASUREMENT_CONTRACT_REVISED = "strategy.measurement_contract.revised"

_UQ_EXPERIMENT_VERSION = "uq_measurement_contract_versions_experiment_version"
_UQ_CLIENT_REQUEST_ID = "uq_measurement_contract_versions_workspace_client_request_id"
_UQ_BINDING_SLOT = "uq_contract_signals_binding_slot"

# Every Contract-level field that participates in material equality (unchanged detection,
# idempotency replay/conflict). The three declaration fields are material: moving a Contract
# from LEGACY to STRUCTURED is a new declaration, not an unchanged revision.
CONTRACT_FIELDS = (
    "measurement_window_days",
    "minimum_evidence",
    "success_criterion",
    "analysis_method_intent",
    "stopping_rule",
    "decision_rule_intent",
) + DECLARATION_CONTRACT_FIELDS
_SIGNAL_FIELDS = (
    "name",
    "description",
    "expected_direction",
    "evidence_requirement",
    "tracking_required",
) + BINDING_FIELDS


def _normalize_signals(signals: list[dict]) -> list[dict]:
    """Outer-trim the two binding strings and treat a blank one as unset — the same
    normalization the request schema applies — so material equality is always over
    NORMALIZED values (idempotent: already-normalized input is unchanged)."""
    normalized = []
    for signal in signals:
        copy = dict(signal)
        for field in ("bound_metric_name", "bound_channel"):
            value = copy.get(field)
            if isinstance(value, str):
                copy[field] = value.strip() or None
        normalized.append(copy)
    return normalized


def _content_equal(
    contract: MeasurementContractVersion,
    signals: list[MeasurementContractRequiredSignal],
    fields: dict,
    requested_signals: list[dict],
) -> bool:
    """Exact, case-sensitive, ORDER-SENSITIVE equality over every
    Contract-level field named in ``CONTRACT_FIELDS`` and the full ordered
    RequiredSignal list (frozen MVP-39B §V), mirroring
    ``ExperimentDefinitionService._content_equal``'s own explicit
    order-sensitivity. Deliberately not a hard-coded field count: additive
    fields (e.g. Pre-Execution Measurement Declaration) join by extending
    ``CONTRACT_FIELDS``/``_SIGNAL_FIELDS``, never this docstring."""
    if not all(getattr(contract, name) == fields[name] for name in CONTRACT_FIELDS):
        return False
    if len(signals) != len(requested_signals):
        return False
    for existing, requested in zip(signals, requested_signals):
        if any(getattr(existing, field) != requested.get(field) for field in _SIGNAL_FIELDS):
            return False
    return True


class ExperimentMeasurementContractService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.strategies = StrategyRepository(session)
        self.hypotheses = HypothesisRepository(session)
        self.experiments = ExperimentRepository(session)
        self.definitions = ExperimentDefinitionRepository(session)
        self.contracts = MeasurementContractRepository(session)
        self.authorizations = ExecutionAuthorizationRepository(session)
        self.starts = ExecutionStartAttestationRepository(session)
        self.events = AuditEventRepository(session)

    # --- reads -------------------------------------------------------------

    def get_current(
        self, *, campaign: Campaign, experiment_public_id: str
    ) -> tuple[Experiment, MeasurementContractVersion | None, list[MeasurementContractRequiredSignal]]:
        """Readable for ANY Experiment of the campaign, including one under a
        superseded Strategy; a Contract-less Experiment yields ``None`` and
        an empty signal list. Non-leaky: unknown/cross-tenant is the same
        ``ForbiddenError``."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        tip = self.contracts.get_tip(experiment_id=experiment.id, workspace_id=campaign.workspace_id)
        signals = self.contracts.list_signals_for_version(contract_version_id=tip.id) if tip is not None else []
        return experiment, tip, signals

    def get_history(
        self, *, campaign: Campaign, experiment_public_id: str
    ) -> tuple[Experiment, list[tuple[MeasurementContractVersion, list[MeasurementContractRequiredSignal]]]]:
        """Ascending version history — unpaginated (mirrors
        ``ExperimentDefinitionService.get_history``'s own accepted
        MVP37B-OBS-2 limit; Contract revisions are expected to be as
        infrequent as Definition revisions)."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        versions = self.contracts.list_for_experiment(experiment_id=experiment.id, workspace_id=campaign.workspace_id)
        return experiment, [
            (version, self.contracts.list_signals_for_version(contract_version_id=version.id)) for version in versions
        ]

    # --- write (the single writer) ------------------------------------------

    def declare_or_revise(
        self,
        *,
        campaign: Campaign,
        experiment_public_id: str,
        base_version: int,
        client_request_id: str,
        definition_version_public_id: str,
        measurement_window_days: int | None,
        minimum_evidence: str | None,
        success_criterion: str | None,
        analysis_method_intent: str | None,
        stopping_rule: str | None,
        decision_rule_intent: str | None,
        signals: list[dict],
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
        declaration_level: str | None = None,
        declaration_semantics_version: int | None = None,
        baseline_window_days: int | None = None,
    ) -> tuple[MeasurementContractVersion, list[MeasurementContractRequiredSignal], ExperimentDefinitionVersion, bool]:
        """Returns ``(contract, signals, pinned_definition, created)``;
        ``created`` is False for a matching replay. Raises
        ``IdempotencyKeyConflictError`` when the key was used for a
        materially different request."""
        experiment = self._resolve_experiment(campaign=campaign, experiment_public_id=experiment_public_id)
        # Plain identifiers, captured before any rollback can expire the ORM objects.
        experiment_id = experiment.id
        workspace_id = campaign.workspace_id
        campaign_id = campaign.id
        hypothesis_id = experiment.hypothesis_id

        requested_definition = self.definitions.get_by_public_id_for_experiment(
            experiment_id=experiment_id, workspace_id=workspace_id, public_id=definition_version_public_id
        )
        if requested_definition is None:  # unknown / foreign / no definition declared: non-leaky
            raise ForbiddenError()
        requested_definition_id = requested_definition.id

        signals = _normalize_signals(signals)
        fields = {
            "measurement_window_days": measurement_window_days,
            "minimum_evidence": minimum_evidence,
            "success_criterion": success_criterion,
            "analysis_method_intent": analysis_method_intent,
            "stopping_rule": stopping_rule,
            "decision_rule_intent": decision_rule_intent,
            "declaration_level": declaration_level,
            "declaration_semantics_version": declaration_semantics_version,
            "baseline_window_days": baseline_window_days,
        }
        self._validate_declaration(fields=fields, signals=signals)

        replay = self._replay(
            workspace_id=workspace_id,
            experiment_id=experiment_id,
            definition_version_id=requested_definition_id,
            base_version=base_version,
            fields=fields,
            signals=signals,
            client_request_id=client_request_id,
        )
        if replay is not None:
            row, signal_rows = replay
            return row, signal_rows, requested_definition, False

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

        replay = self._replay(
            workspace_id=workspace_id,
            experiment_id=experiment_id,
            definition_version_id=requested_definition_id,
            base_version=base_version,
            fields=fields,
            signals=signals,
            client_request_id=client_request_id,
        )
        if replay is not None:
            row, signal_rows = replay
            return row, signal_rows, requested_definition, False

        current_strategy = self.strategies.get_current_for_campaign(campaign_id)
        if current_strategy is None or current_strategy.id != strategy_id:
            raise MeasurementContractStrategyStaleError()

        contract_tip = self.contracts.get_tip(experiment_id=experiment_id, workspace_id=workspace_id)
        contract_tip_version = contract_tip.version if contract_tip is not None else 0
        if base_version != contract_tip_version:
            raise MeasurementContractBaseStaleError()

        # Governed Execution Start (EXAUTH-DF-OBS-1, model C1): ANY Start for
        # this Experiment freezes the Contract lineage PERMANENTLY, even after
        # the Authorization is revoked. Checked BEFORE the active-Authorization
        # freeze so the error code is deterministic (revocation resolves only
        # the latter).
        if self.starts.exists_for_experiment(experiment_id=experiment_id, workspace_id=workspace_id):
            raise MeasurementContractFrozenByExecutionStartError()

        if contract_tip is not None and self.authorizations.exists_active_for_contract_version(
            contract_version_id=contract_tip.id
        ):
            raise MeasurementContractFrozenByAuthorizationError()

        definition_tip = self.definitions.get_tip(experiment_id=experiment_id, workspace_id=workspace_id)
        if definition_tip is None or definition_tip.id != requested_definition_id:
            raise MeasurementContractDefinitionVersionNotCurrentError()

        # Pre-Execution Measurement Declaration: CONTROLLED measurement is future scope, so a
        # structured declaration may not be attached to a CONTROLLED comparison. Checked BEFORE
        # the success-criterion requirement so the error code is deterministic.
        if declaration_level is not None and definition_tip.comparison_type == ComparisonType.CONTROLLED.value:
            raise MeasurementContractControlledDeclarationNotSupportedError()

        if definition_tip.comparison_type == ComparisonType.CONTROLLED.value and success_criterion is None:
            raise MeasurementContractSuccessCriterionRequiredError()

        if contract_tip is not None:
            existing_signals = self.contracts.list_signals_for_version(contract_version_id=contract_tip.id)
            if _content_equal(contract_tip, existing_signals, fields, signals):
                raise MeasurementContractUnchangedError()

        new_version = contract_tip_version + 1
        try:
            row, signal_rows = self.contracts.create(
                experiment_id=experiment_id,
                workspace_id=workspace_id,
                definition_version_id=requested_definition_id,
                version=new_version,
                client_request_id=client_request_id,
                signals=signals,
                **fields,
            )
            self.events.record(
                workspace_id=workspace_id,
                event_type=(
                    EVENT_MEASUREMENT_CONTRACT_DECLARED if new_version == 1 else EVENT_MEASUREMENT_CONTRACT_REVISED
                ),
                actor_type=ActorType.USER,
                campaign_id=campaign_id,
                strategy_id=strategy_id,
                hypothesis_id=hypothesis_id,
                experiment_id=experiment_id,
                experiment_definition_version_id=requested_definition_id,
                measurement_contract_id=row.id,
                previous_state=f"v{contract_tip_version}" if contract_tip_version else None,
                new_state=f"v{new_version}",
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == _UQ_EXPERIMENT_VERSION:
                raise MeasurementContractBaseStaleError() from None
            if constraint == _UQ_BINDING_SLOT:
                # Unreachable through this writer (the pure duplicate-slot check runs first);
                # the database index is the backstop and is translated, never a 500.
                raise MeasurementContractBindingConflictError() from None
            if constraint == _UQ_CLIENT_REQUEST_ID:
                replay = self._replay(
                    workspace_id=workspace_id,
                    experiment_id=experiment_id,
                    definition_version_id=requested_definition_id,
                    base_version=base_version,
                    fields=fields,
                    signals=signals,
                    client_request_id=client_request_id,
                )
                if replay is not None:
                    replay_row, replay_signals = replay
                    return replay_row, replay_signals, requested_definition, False
            raise
        return row, signal_rows, requested_definition, True

    # --- internals -----------------------------------------------------------

    @staticmethod
    def _validate_declaration(*, fields: dict, signals: list[dict]) -> None:
        """The service-level (cross-row) declaration rules: completeness of the whole
        declaration against its level, and binding-slot ownership. Pure — it reads no
        Definition, claim or evidence. The request schema runs the same shape function."""
        problems = declaration_shape_problems(
            declaration_level=fields["declaration_level"],
            declaration_semantics_version=fields["declaration_semantics_version"],
            measurement_window_days=fields["measurement_window_days"],
            baseline_window_days=fields["baseline_window_days"],
            signals=signals,
        )
        if problems:
            raise MeasurementContractDeclarationInvalidError(" ".join(problems))
        if first_duplicate_binding_slot(signals) is not None:
            raise MeasurementContractBindingConflictError(
                "Two required signals declare the same metric and channel binding."
            )
        if first_binding_ownership_conflict(signals) is not None:
            raise MeasurementContractBindingConflictError(
                "A metric cannot be bound both to any channel and to an exact channel in one measurement contract."
            )

    def _resolve_experiment(self, *, campaign: Campaign, experiment_public_id: str) -> Experiment:
        experiment = self.experiments.get_for_campaign_by_public_id(
            campaign_id=campaign.id, public_id=experiment_public_id
        )
        if experiment is None or experiment.workspace_id != campaign.workspace_id:
            raise ForbiddenError()
        return experiment

    def _replay(
        self,
        *,
        workspace_id: uuid.UUID,
        experiment_id: uuid.UUID,
        definition_version_id: uuid.UUID,
        base_version: int,
        fields: dict,
        signals: list[dict],
        client_request_id: str,
    ) -> tuple[MeasurementContractVersion, list[MeasurementContractRequiredSignal]] | None:
        """None when the key is unused; ``(row, signals)`` when the key was
        used for a materially equal request (same Experiment, same pinned
        Definition version, same ``base_version``, same six Contract fields,
        same ordered RequiredSignal list); otherwise a key conflict."""
        existing = self.contracts.get_by_workspace_and_request_id(
            workspace_id=workspace_id, client_request_id=client_request_id
        )
        if existing is None:
            return None
        if (
            existing.experiment_id == experiment_id
            and existing.definition_version_id == definition_version_id
            and existing.version - 1 == base_version
        ):
            existing_signals = self.contracts.list_signals_for_version(contract_version_id=existing.id)
            if _content_equal(existing, existing_signals, fields, signals):
                return existing, existing_signals
        raise IdempotencyKeyConflictError()
