"""Public DTOs for the strategy read surface. Never expose an internal
UUID or a raw ``workspace_id`` — only public_id-derived fields (BACKEND-08
§19, mirroring ``app/research/schemas.py``).

No field here ever represents approval, maturity, a Strategic Decision, or
a chain-of-thought/reasoning trace — see ``app/strategy/models.py`` for why
none of those exist on the underlying models in the first place.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.measurement.models import MetricSource
from app.strategy.models import (
    EXECUTION_AUTHORIZATION_ALLOCATION_DESIGN_MAX_LENGTH,
    EXECUTION_AUTHORIZATION_CLIENT_REQUEST_ID_MAX_LENGTH,
    EXECUTION_AUTHORIZATION_REVOKED_REASON_MAX_LENGTH,
    EXECUTION_AUTHORIZATION_UNIT_OF_ASSIGNMENT_MAX_LENGTH,
    EXECUTION_START_CLIENT_REQUEST_ID_MAX_LENGTH,
    EXPERIMENT_DEFINITION_CLIENT_REQUEST_ID_MAX_LENGTH,
    EXPERIMENT_DEFINITION_FACTOR_MAX_LENGTH,
    EXPERIMENT_DEFINITION_MAX_CONTROLLED_FACTORS,
    EXPERIMENT_DEFINITION_PROSE_MAX_LENGTH,
    EXPERIMENT_EVIDENCE_CLAIM_CLIENT_REQUEST_ID_MAX_LENGTH,
    EXPERIMENT_EVIDENCE_CLAIM_DISPOSAL_REASON_MAX_LENGTH,
    EXPERIMENT_EVIDENCE_CLAIM_METRIC_NAME_MAX_LENGTH,
    EXPERIMENT_VARIANT_DESCRIPTION_MAX_LENGTH,
    EXPERIMENT_VARIANT_LABEL_MAX_LENGTH,
    MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH,
    MEASUREMENT_CONTRACT_SIGNAL_DESCRIPTION_MAX_LENGTH,
    MEASUREMENT_CONTRACT_SIGNAL_NAME_MAX_LENGTH,
    ComparisonType,
    Experiment,
    ExecutionAuthorization,
    ExperimentDefinitionVersion,
    ExperimentVariant,
    ExpectedDirection,
    Hypothesis,
    MeasurementContractRequiredSignal,
    MeasurementContractVersion,
    Positioning,
    Strategy,
)

_HYPOTHESIS_STATEMENT_MAX_LENGTH = 4000
_EXPERIMENT_DESCRIPTION_MAX_LENGTH = 4000


class PositioningPublic(BaseModel):
    id: str
    statement: str
    created_at: datetime


class HypothesisPublic(BaseModel):
    id: str
    statement: str
    status: str
    created_at: datetime


class CreateHypothesisRequest(BaseModel):
    """MVP-31A §7/§O (frozen contract, implemented MVP-31B): the minimum
    client assertion for a governed Hypothesis — the complete new
    statement, nothing else. No ``status``, ``strategy_id``,
    ``workspace_id``, ``campaign_id``, ``origin``, ``created_at``, or
    audit actor is ever accepted from the client — all server-derived or
    server-controlled (``status`` always starts ``OPEN``, MVP-31A §17)."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=_HYPOTHESIS_STATEMENT_MAX_LENGTH)

    @field_validator("statement")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("This field cannot be blank.")
        return stripped


# MVP-37 (frozen MVP-37B §Y/§J): derived, read-time labels and restriction
# codes. Never persisted. A label states that a comparison was DECLARED —
# never that it is valid, causal, or has a result/winner.
LABEL_NO_COMPARISON_DECLARED = "NO_COMPARISON_DECLARED"
LABEL_DECLARED_OBSERVATIONAL_INTENT = "DECLARED_OBSERVATIONAL_INTENT"
LABEL_DECLARED_CONTROLLED_INTENT = "DECLARED_CONTROLLED_INTENT"

_NON_CONCLUSION_CODES_ALL = (
    "NO_ATTRIBUTION_ESTABLISHED",
    "NO_STATISTICAL_VALIDITY_ESTABLISHED",
    "NO_RESULT_OR_WINNER",
)
_NON_CONCLUSION_CODES_BY_TYPE = {
    ComparisonType.OBSERVATIONAL.value: ("CANNOT_ESTABLISH_CAUSALITY",),
    ComparisonType.CONTROLLED.value: (
        "CAUSALITY_NOT_ESTABLISHED_BY_DEFINITION",
        "CONTROLLED_VALIDITY_NOT_ESTABLISHED",
    ),
}


def comparison_label_for(definition: ExperimentDefinitionVersion | None) -> str:
    if definition is None:
        return LABEL_NO_COMPARISON_DECLARED
    if definition.comparison_type == ComparisonType.CONTROLLED.value:
        return LABEL_DECLARED_CONTROLLED_INTENT
    return LABEL_DECLARED_OBSERVATIONAL_INTENT


def non_conclusion_codes_for(comparison_type: str) -> list[str]:
    return [*_NON_CONCLUSION_CODES_ALL, *_NON_CONCLUSION_CODES_BY_TYPE.get(comparison_type, ())]


class ExperimentDefinitionPublic(BaseModel):
    """MVP-37: one immutable Definition version. No validity/causal/result
    field exists — ``non_conclusion_codes`` is derived response-only
    metadata (never stored)."""

    id: str
    experiment_id: str
    version: int
    comparison_question: str
    comparison_type: str
    changed_factor: str
    controlled_factors: list[str]
    comparison_basis: str
    scope: str
    learning_intent: str
    non_conclusion_boundary: str
    non_conclusion_codes: list[str]
    created_at: datetime
    # MVP-38 (additive, derived, NEVER stored): how many Variants pin this
    # version, and whether any governed pinning child exists. ``is_pinned``
    # means only that — it is a lock indicator, never a validity/readiness/
    # causality claim, and it never changes ``comparison_label``.
    variant_count: int = 0
    # MVP-39 (additive, derived, NEVER stored): ``is_pinned`` is WIDENED —
    # true whenever EITHER a Variant OR a Measurement Contract exists for
    # this version (MVP-39B §17/§38). ``variant_count`` keeps its exact
    # MVP-38 meaning; a Contract never increments it.
    is_pinned: bool = False
    has_measurement_contract: bool = False
    measurement_contract_version: int | None = None


class ExperimentPublic(BaseModel):
    id: str
    hypothesis_id: str
    description: str
    status: str | None
    created_at: datetime
    # MVP-37 (additive): the current definition tip, or null; the label is
    # always present.
    comparison_label: str = LABEL_NO_COMPARISON_DECLARED
    definition: ExperimentDefinitionPublic | None = None


class CreateExperimentRequest(BaseModel):
    """MVP-32A §7/§21/MVP-32A-R1 (frozen contract, implemented MVP-32B):
    the minimum client assertion for a governed Experiment — the complete
    test-design/mechanism description, nothing else. No ``hypothesis_id``,
    ``strategy_id``, ``workspace_id``, ``campaign_id``, ``status``,
    ``actor``, ``origin``, ``variant``, ``learning_intent``,
    ``controlled_variable``, ``measurement_requirement``, or any
    execution field is ever accepted from the client — all server-derived
    or server-controlled (``status`` always starts ``"RECORDED"``,
    MVP-32A §11)."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=_EXPERIMENT_DESCRIPTION_MAX_LENGTH)

    @field_validator("description")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("This field cannot be blank.")
        return stripped


def _factor_key(value: str) -> str:
    """Comparison key used ONLY to detect duplicate / overlapping factors
    (frozen MVP-37B §V); never used to rewrite stored content."""
    return " ".join(value.split()).casefold()


def _strip_if_str(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _reject_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("NUL characters are not allowed.")
    return value


def _require_single_line(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("This field must be a single line.")
    return value


class DeclareExperimentDefinitionRequest(BaseModel):
    """MVP-37B §B/§N/§O: the FULL-STATE version-write payload — the eight
    Definition fields plus ``base_version`` (0 for the first declaration,
    otherwise the current tip) and ``client_request_id``. No measurement,
    allocation, Variant, result or execution field is accepted
    (``extra="forbid"``). ``workspace_id``/``experiment_id``/``version``/
    ``actor`` are all server-derived."""

    model_config = ConfigDict(extra="forbid")

    base_version: int = Field(strict=True, ge=0, le=2_147_483_646)
    client_request_id: str = Field(min_length=1, max_length=EXPERIMENT_DEFINITION_CLIENT_REQUEST_ID_MAX_LENGTH)
    comparison_question: str = Field(min_length=1, max_length=EXPERIMENT_DEFINITION_PROSE_MAX_LENGTH)
    comparison_type: ComparisonType
    changed_factor: str = Field(min_length=1, max_length=EXPERIMENT_DEFINITION_FACTOR_MAX_LENGTH)
    controlled_factors: list[str] = Field(max_length=EXPERIMENT_DEFINITION_MAX_CONTROLLED_FACTORS)
    comparison_basis: str = Field(min_length=1, max_length=EXPERIMENT_DEFINITION_PROSE_MAX_LENGTH)
    scope: str = Field(min_length=1, max_length=EXPERIMENT_DEFINITION_PROSE_MAX_LENGTH)
    learning_intent: str = Field(min_length=1, max_length=EXPERIMENT_DEFINITION_PROSE_MAX_LENGTH)
    non_conclusion_boundary: str = Field(min_length=1, max_length=EXPERIMENT_DEFINITION_PROSE_MAX_LENGTH)

    @field_validator(
        "comparison_question",
        "changed_factor",
        "comparison_basis",
        "scope",
        "learning_intent",
        "non_conclusion_boundary",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        return _strip_if_str(value)

    @field_validator("controlled_factors", mode="before")
    @classmethod
    def _strip_factors(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [_strip_if_str(item) for item in value]
        return value

    @field_validator("client_request_id")
    @classmethod
    def _client_request_id_rules(cls, value: str) -> str:
        # No normalization (same as commercial); NUL is still rejected —
        # PostgreSQL text cannot store it and it would otherwise surface as a 500.
        return _reject_nul(value)

    @field_validator(
        "comparison_question", "comparison_basis", "scope", "learning_intent", "non_conclusion_boundary"
    )
    @classmethod
    def _prose_rules(cls, value: str) -> str:
        return _reject_nul(value)

    @field_validator("changed_factor")
    @classmethod
    def _changed_factor_rules(cls, value: str) -> str:
        return _require_single_line(_reject_nul(value))

    @field_validator("controlled_factors")
    @classmethod
    def _controlled_factor_rules(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        for item in value:
            if not item or len(item) > EXPERIMENT_DEFINITION_FACTOR_MAX_LENGTH:
                raise ValueError(
                    f"Each controlled factor must be 1 to {EXPERIMENT_DEFINITION_FACTOR_MAX_LENGTH} characters."
                )
            _require_single_line(_reject_nul(item))
            key = _factor_key(item)
            if key in seen:
                raise ValueError("Controlled factors must not contain duplicates.")
            seen.add(key)
        return value

    @model_validator(mode="after")
    def _cross_field_rules(self) -> "DeclareExperimentDefinitionRequest":
        if self.comparison_type is ComparisonType.CONTROLLED and not self.controlled_factors:
            raise ValueError("A CONTROLLED comparison requires at least one controlled factor.")
        changed_key = _factor_key(self.changed_factor)
        if any(_factor_key(item) == changed_key for item in self.controlled_factors):
            raise ValueError("The changed factor must not also be a controlled factor.")
        return self


class StrategyPublic(BaseModel):
    id: str
    campaign_id: str
    version: int
    # MVP-30A-R1: "BOOTSTRAP" or "REVISION" — lets a caller distinguish a
    # legacy/deterministic-bootstrap version from a governed Revision
    # without needing to separately fetch its StrategyRevision provenance.
    origin: str
    summary: str
    created_at: datetime


class StrategyOutputResponse(BaseModel):
    strategy: StrategyPublic | None
    positioning: PositioningPublic | None
    hypotheses: list[HypothesisPublic]
    experiments: list[ExperimentPublic]


def strategy_to_public(strategy: Strategy, *, campaign_public_id: str) -> StrategyPublic:
    return StrategyPublic(
        id=strategy.public_id,
        campaign_id=campaign_public_id,
        version=strategy.version,
        origin=strategy.origin.value,
        summary=strategy.summary,
        created_at=strategy.created_at,
    )


def positioning_to_public(positioning: Positioning) -> PositioningPublic:
    return PositioningPublic(
        id=positioning.public_id,
        statement=positioning.statement,
        created_at=positioning.created_at,
    )


def hypothesis_to_public(hypothesis: Hypothesis) -> HypothesisPublic:
    return HypothesisPublic(
        id=hypothesis.public_id,
        statement=hypothesis.statement,
        status=hypothesis.status.value,
        created_at=hypothesis.created_at,
    )


def definition_to_public(
    definition: ExperimentDefinitionVersion,
    *,
    experiment_public_id: str,
    pin_state: tuple[int, bool, bool, int | None] = (0, False, False, None),
) -> ExperimentDefinitionPublic:
    """``pin_state`` is ``(variant_count, is_pinned, has_measurement_contract,
    measurement_contract_version)`` — derived by
    ``ExperimentDefinitionService.pin_states_for_versions`` (batched)."""
    return ExperimentDefinitionPublic(
        id=definition.public_id,
        experiment_id=experiment_public_id,
        version=definition.version,
        comparison_question=definition.comparison_question,
        comparison_type=definition.comparison_type,
        changed_factor=definition.changed_factor,
        controlled_factors=list(definition.controlled_factors),
        comparison_basis=definition.comparison_basis,
        scope=definition.scope,
        learning_intent=definition.learning_intent,
        non_conclusion_boundary=definition.non_conclusion_boundary,
        non_conclusion_codes=non_conclusion_codes_for(definition.comparison_type),
        created_at=definition.created_at,
        variant_count=pin_state[0],
        is_pinned=pin_state[1],
        has_measurement_contract=pin_state[2],
        measurement_contract_version=pin_state[3],
    )


def experiment_to_public(
    experiment: Experiment,
    *,
    hypothesis_public_id: str,
    definition: ExperimentDefinitionVersion | None = None,
    definition_pin_state: tuple[int, bool, bool, int | None] = (0, False, False, None),
) -> ExperimentPublic:
    """``definition`` is the Experiment's current definition tip (or None).
    An Experiment with no definition serializes ``definition=None`` and
    ``comparison_label="NO_COMPARISON_DECLARED"``."""
    return ExperimentPublic(
        id=experiment.public_id,
        hypothesis_id=hypothesis_public_id,
        description=experiment.description,
        status=experiment.status,
        created_at=experiment.created_at,
        comparison_label=comparison_label_for(definition),
        definition=(
            definition_to_public(
                definition, experiment_public_id=experiment.public_id, pin_state=definition_pin_state
            )
            if definition is not None
            else None
        ),
    )


class ExperimentDefinitionHistoryResponse(BaseModel):
    experiment_id: str
    comparison_label: str
    current_version: int | None
    versions: list[ExperimentDefinitionPublic]


class CreateVariantRequest(BaseModel):
    """MVP-38B §R/§J: the Variant declaration payload. ``definition_version_id``
    is the EXPLICIT pin (the public ``EXD-…`` id) — the server never
    substitutes the current tip. No ``role``, ``weight``, ``traffic``,
    ``metric``, ``status``, allocation, exposure or result field is accepted
    (``extra="forbid"``). ``workspace_id``/``experiment_id``/``ordinal``/
    actor are all server-derived."""

    model_config = ConfigDict(extra="forbid")

    definition_version_id: str = Field(min_length=1, max_length=20)
    label: str = Field(min_length=1, max_length=EXPERIMENT_VARIANT_LABEL_MAX_LENGTH)
    condition_description: str = Field(min_length=1, max_length=EXPERIMENT_VARIANT_DESCRIPTION_MAX_LENGTH)
    client_request_id: str = Field(min_length=1, max_length=EXPERIMENT_DEFINITION_CLIENT_REQUEST_ID_MAX_LENGTH)

    @field_validator("definition_version_id", "label", "condition_description", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        return _strip_if_str(value)

    @field_validator("definition_version_id")
    @classmethod
    def _version_id_rules(cls, value: str) -> str:
        return _reject_nul(value)

    @field_validator("client_request_id")
    @classmethod
    def _client_request_id_rules(cls, value: str) -> str:
        return _reject_nul(value)

    @field_validator("label")
    @classmethod
    def _label_rules(cls, value: str) -> str:
        return _require_single_line(_reject_nul(value))

    @field_validator("condition_description")
    @classmethod
    def _description_rules(cls, value: str) -> str:
        return _reject_nul(value)


class VariantPublic(BaseModel):
    """MVP-38: one immutable Variant. Public ids only (``VAR-…``, ``EXP-…``,
    ``EXD-…``) — no internal UUID. No role, weight, status, metric, result or
    validity field exists."""

    id: str
    experiment_id: str
    definition_version_id: str
    ordinal: int
    label: str
    condition_description: str
    created_at: datetime


class VariantListResponse(BaseModel):
    experiment_id: str
    items: list[VariantPublic]
    limit: int
    offset: int
    total: int


def variant_to_public(
    variant: ExperimentVariant, *, experiment_public_id: str, definition_version_public_id: str
) -> VariantPublic:
    return VariantPublic(
        id=variant.public_id,
        experiment_id=experiment_public_id,
        definition_version_id=definition_version_public_id,
        ordinal=variant.ordinal,
        label=variant.label,
        condition_description=variant.condition_description,
        created_at=variant.created_at,
    )


# MVP-39 (frozen MVP-39A/-39B): derived, read-time labels. Never persisted.
# A label states that a Measurement Contract was DECLARED — never that it
# is frozen, authorized, ready, or that evidence exists (MVP-39B §7/§39).
LABEL_NO_MEASUREMENT_CONTRACT_DECLARED = "NO_MEASUREMENT_CONTRACT_DECLARED"
LABEL_DECLARED_MEASUREMENT_INTENT = "DECLARED_MEASUREMENT_INTENT"


def measurement_contract_label_for(contract: "MeasurementContractVersion | None") -> str:
    return LABEL_NO_MEASUREMENT_CONTRACT_DECLARED if contract is None else LABEL_DECLARED_MEASUREMENT_INTENT


def _signal_key(value: str) -> str:
    """Comparison key used ONLY to detect logically duplicate RequiredSignal
    names within one request (frozen MVP-39B §25); never stored, never used
    to rewrite a name. Identical shape to ``_factor_key`` above and to
    ``app/strategy/variant_service.py::label_key``."""
    return " ".join(value.split()).casefold()


def _optional_prose(value: Any) -> Any:
    """"Before" coercion shared by every optional prose field in this
    module: outer-trim, and an empty result becomes ``None`` rather than a
    validation error — an optional field submitted blank is simply unset."""
    if value is None or not isinstance(value, str):
        return value
    stripped = value.strip()
    return stripped if stripped else None


class RequiredSignalRequest(BaseModel):
    """MVP-39B §I/§K: one declared RequiredSignal within a Measurement
    Contract write — a first-class identity, never evidence itself. No
    ``observed_value``/``score``/threshold/operator field is accepted
    (``extra="forbid"``): that would smuggle Result-governance semantics
    into a pre-execution declaration (MVP-39B §O/§AN). ``tracking_required``
    is declarative only, never an FK (MVP-39B §AL)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MEASUREMENT_CONTRACT_SIGNAL_NAME_MAX_LENGTH)
    description: str = Field(min_length=1, max_length=MEASUREMENT_CONTRACT_SIGNAL_DESCRIPTION_MAX_LENGTH)
    expected_direction: ExpectedDirection | None = None
    evidence_requirement: str | None = Field(default=None, max_length=MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH)
    tracking_required: bool = False

    @field_validator("name", "description", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> Any:
        return _strip_if_str(value)

    @field_validator("evidence_requirement", mode="before")
    @classmethod
    def _evidence_requirement_before(cls, value: Any) -> Any:
        return _optional_prose(value)

    @field_validator("name")
    @classmethod
    def _name_rules(cls, value: str) -> str:
        return _require_single_line(_reject_nul(value))

    @field_validator("description")
    @classmethod
    def _description_rules(cls, value: str) -> str:
        return _reject_nul(value)

    @field_validator("evidence_requirement")
    @classmethod
    def _evidence_requirement_rules(cls, value: str | None) -> str | None:
        return _reject_nul(value) if value is not None else None


class DeclareMeasurementContractRequest(BaseModel):
    """MVP-39B: the FULL-STATE Measurement Contract version write payload —
    mirrors ``DeclareExperimentDefinitionRequest``'s own shape one level
    down. The explicit tip pin (``definition_version_id``, an ``EXD-…``
    id) is never silently substituted (MVP-39B §Y/§21), mirroring
    ``CreateVariantRequest``. No allocation, exposure, tracking-
    implementation, winner, result, or execution-authorization field is
    ever accepted (``extra="forbid"``). At least one RequiredSignal is
    required at the schema layer (``min_length=1``) — the database itself
    cannot enforce this cross-row minimum (MVP39B-OBS-1)."""

    model_config = ConfigDict(extra="forbid")

    base_version: int = Field(strict=True, ge=0, le=2_147_483_646)
    client_request_id: str = Field(min_length=1, max_length=EXPERIMENT_DEFINITION_CLIENT_REQUEST_ID_MAX_LENGTH)
    definition_version_id: str = Field(min_length=1, max_length=20)
    measurement_window_days: int | None = Field(default=None, strict=True, ge=1, le=2_147_483_646)
    minimum_evidence: str | None = Field(default=None, max_length=MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH)
    success_criterion: str | None = Field(default=None, max_length=MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH)
    analysis_method_intent: str | None = Field(default=None, max_length=MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH)
    stopping_rule: str | None = Field(default=None, max_length=MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH)
    decision_rule_intent: str | None = Field(default=None, max_length=MEASUREMENT_CONTRACT_PROSE_MAX_LENGTH)
    signals: list[RequiredSignalRequest] = Field(min_length=1)

    @field_validator("client_request_id", "definition_version_id", mode="before")
    @classmethod
    def _strip_ids(cls, value: Any) -> Any:
        return _strip_if_str(value)

    @field_validator("client_request_id")
    @classmethod
    def _client_request_id_rules(cls, value: str) -> str:
        return _reject_nul(value)

    @field_validator("definition_version_id")
    @classmethod
    def _definition_version_id_rules(cls, value: str) -> str:
        return _reject_nul(value)

    @field_validator(
        "minimum_evidence", "success_criterion", "analysis_method_intent", "stopping_rule", "decision_rule_intent",
        mode="before",
    )
    @classmethod
    def _optional_prose_before(cls, value: Any) -> Any:
        return _optional_prose(value)

    @field_validator(
        "minimum_evidence", "success_criterion", "analysis_method_intent", "stopping_rule", "decision_rule_intent"
    )
    @classmethod
    def _optional_prose_rules(cls, value: str | None) -> str | None:
        return _reject_nul(value) if value is not None else None

    @model_validator(mode="after")
    def _signal_duplicate_rules(self) -> "DeclareMeasurementContractRequest":
        seen: set[str] = set()
        for signal in self.signals:
            key = _signal_key(signal.name)
            if key in seen:
                raise ValueError("Required signal names must not contain duplicates.")
            seen.add(key)
        return self


class RequiredSignalPublic(BaseModel):
    """MVP-39: one immutable RequiredSignal. Public ids only (``RSG-…``) —
    no internal UUID. No observed value, score, or result field exists."""

    id: str
    ordinal: int
    name: str
    description: str
    expected_direction: str | None
    evidence_requirement: str | None
    tracking_required: bool


class MeasurementContractPublic(BaseModel):
    """MVP-39: one immutable Measurement Contract version. Public ids only
    (``MSC-…``, ``EXP-…``, ``EXD-…``) — no internal UUID. No status,
    frozen_at, execution_authorized, winner, or result field exists."""

    id: str
    experiment_id: str
    definition_version_id: str
    version: int
    measurement_window_days: int | None
    minimum_evidence: str | None
    success_criterion: str | None
    analysis_method_intent: str | None
    stopping_rule: str | None
    decision_rule_intent: str | None
    signals: list[RequiredSignalPublic]
    created_at: datetime


class MeasurementContractHistoryResponse(BaseModel):
    experiment_id: str
    measurement_contract_label: str
    current_version: int | None
    versions: list[MeasurementContractPublic]


def required_signal_to_public(signal: MeasurementContractRequiredSignal) -> RequiredSignalPublic:
    return RequiredSignalPublic(
        id=signal.public_id,
        ordinal=signal.ordinal,
        name=signal.name,
        description=signal.description,
        expected_direction=signal.expected_direction,
        evidence_requirement=signal.evidence_requirement,
        tracking_required=signal.tracking_required,
    )


def measurement_contract_to_public(
    contract: MeasurementContractVersion,
    *,
    experiment_public_id: str,
    definition_version_public_id: str,
    signals: list[MeasurementContractRequiredSignal],
) -> MeasurementContractPublic:
    return MeasurementContractPublic(
        id=contract.public_id,
        experiment_id=experiment_public_id,
        definition_version_id=definition_version_public_id,
        version=contract.version,
        measurement_window_days=contract.measurement_window_days,
        minimum_evidence=contract.minimum_evidence,
        success_criterion=contract.success_criterion,
        analysis_method_intent=contract.analysis_method_intent,
        stopping_rule=contract.stopping_rule,
        decision_rule_intent=contract.decision_rule_intent,
        signals=[required_signal_to_public(signal) for signal in signals],
        created_at=contract.created_at,
    )


# MVP-40 (frozen Execution Authorization Design Freeze §B/§D/§AF): the
# client never supplies definition_version_id/contract_version_id/Variant
# ids — Authorization always pins whatever is CURRENT at write time. No
# allocation, assignment, exposure, tracking, target, validity, or
# execution-start field is ever accepted (``extra="forbid"``).
class AuthorizeExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=1, max_length=EXECUTION_AUTHORIZATION_CLIENT_REQUEST_ID_MAX_LENGTH)
    unit_of_assignment: str = Field(min_length=1, max_length=EXECUTION_AUTHORIZATION_UNIT_OF_ASSIGNMENT_MAX_LENGTH)
    allocation_design: str = Field(min_length=1, max_length=EXECUTION_AUTHORIZATION_ALLOCATION_DESIGN_MAX_LENGTH)

    @field_validator("client_request_id", "unit_of_assignment", "allocation_design", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        return _strip_if_str(value)

    @field_validator("client_request_id")
    @classmethod
    def _client_request_id_rules(cls, value: str) -> str:
        return _reject_nul(value)

    @field_validator("unit_of_assignment")
    @classmethod
    def _unit_of_assignment_rules(cls, value: str) -> str:
        return _require_single_line(_reject_nul(value))

    @field_validator("allocation_design")
    @classmethod
    def _allocation_design_rules(cls, value: str) -> str:
        return _reject_nul(value)


# Governed Execution Start: ``started_at`` must stay decodable, not merely
# storable (the same lesson as CommercialOutcome.occurred_at, MVP-36B-R2/R3).
# Locally defined — no cross-domain sharing. Eight whole calendar days are
# reserved at each end so a UTC-offset input can never overflow decoding; the
# service's own floor (the Authorization's creation) is far tighter anyway.
_STARTED_AT_MIN = datetime(1, 1, 9, tzinfo=timezone.utc)
_STARTED_AT_MAX = datetime(9999, 12, 23, 23, 59, 59, 999999, tzinfo=timezone.utc)


def _require_decodable_started_at(value: datetime) -> datetime:
    if value < _STARTED_AT_MIN or value > _STARTED_AT_MAX:
        raise ValueError(
            "started_at must be between 0001-01-09T00:00:00Z and 9999-12-23T23:59:59.999999Z (UTC instant)"
        )
    return value


StartedAt = Annotated[AwareDatetime, AfterValidator(_require_decodable_started_at)]


class StartExecutionRequest(BaseModel):
    """Governed Execution Start: exactly ``client_request_id`` and the
    operator-attested ``started_at`` (timezone-aware; a naive datetime is
    rejected). ``extra="forbid"`` — no assignment, unit, cohort, exposure,
    evidence, note or reference field is ever accepted."""

    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=1, max_length=EXECUTION_START_CLIENT_REQUEST_ID_MAX_LENGTH)
    started_at: StartedAt

    @field_validator("client_request_id", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        return _strip_if_str(value)

    @field_validator("client_request_id")
    @classmethod
    def _client_request_id_rules(cls, value: str) -> str:
        return _reject_nul(value)


class RevokeExecutionAuthorizationRequest(BaseModel):
    """MVP-40 (frozen Design Freeze §L/§19): reason is required — pairs with
    the DB-level ``revocation_pairing`` CHECK on ``revoked_at``/
    ``revoked_reason``."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=EXECUTION_AUTHORIZATION_REVOKED_REASON_MAX_LENGTH)

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value: Any) -> Any:
        return _strip_if_str(value)

    @field_validator("reason")
    @classmethod
    def _reason_rules(cls, value: str) -> str:
        return _reject_nul(value)


class ExecutionAuthorizationVariantPublic(BaseModel):
    """MVP-40: one snapshotted Variant reference within an Authorization's
    Variant-set — the durable ``ExperimentVariant`` row remains the sole
    source of label/description (frozen §G)."""

    id: str
    label: str
    condition_description: str


class ExecutionStartPublic(BaseModel):
    """Governed Execution Start: the embedded, immutable human attestation
    that execution of ONE Authorization began. ``started_at`` is the
    operator-ATTESTED instant (future window-anchor candidate);
    ``created_at`` is the SERVER record time (chronology/audit ordering) —
    both are always exposed and never interchangeable. No executing/
    completed/valid/successful field exists: this is attestation only, never
    verified external execution, assignment, delivery, exposure, evidence or
    a result."""

    id: str
    started_at: datetime
    created_at: datetime


class ExecutionAuthorizationPublic(BaseModel):
    """MVP-40: one immutable Execution Authorization. Public ids only — no
    internal UUID. No status/assignment/exposure/tracking-valid/
    measurement-ready/result/winner field exists anywhere — ``active`` is the
    only derived state, exactly ``revoked_at IS NULL``. The single nullable
    ``execution_start`` object embeds the human attestation that execution of
    THIS Authorization began (Governed Execution Start); it is never a
    verified fact, never an executing/completed/valid state."""

    id: str
    experiment_id: str
    definition_version_id: str
    contract_version_id: str
    unit_of_assignment: str
    allocation_design: str
    variants: list[ExecutionAuthorizationVariantPublic]
    # Informational only (frozen T4): counts of the pinned Contract's declared
    # signals — never a claim that tracking exists, is implemented or valid.
    signal_count: int
    tracking_required_signal_count: int
    active: bool
    revoked_at: datetime | None
    revoked_reason: str | None
    superseded_by: str | None
    # Governed Execution Start: null until a Start is attested. The ONLY new
    # field — no executing/completed/valid/successful state is exposed.
    execution_start: ExecutionStartPublic | None = None
    created_at: datetime


class ExecutionAuthorizationHistoryResponse(BaseModel):
    experiment_id: str
    current_id: str | None
    authorizations: list[ExecutionAuthorizationPublic]


def execution_authorization_to_public(
    authorization: ExecutionAuthorization,
    *,
    experiment_public_id: str,
    definition_version_public_id: str,
    contract_version_public_id: str,
    variants: list[ExecutionAuthorizationVariantPublic],
    signal_count: int,
    tracking_required_signal_count: int,
    superseded_by_public_id: str | None,
    execution_start: ExecutionStartPublic | None = None,
) -> ExecutionAuthorizationPublic:
    return ExecutionAuthorizationPublic(
        id=authorization.public_id,
        experiment_id=experiment_public_id,
        definition_version_id=definition_version_public_id,
        contract_version_id=contract_version_public_id,
        unit_of_assignment=authorization.unit_of_assignment,
        allocation_design=authorization.allocation_design,
        variants=variants,
        signal_count=signal_count,
        tracking_required_signal_count=tracking_required_signal_count,
        active=authorization.revoked_at is None,
        revoked_at=authorization.revoked_at,
        revoked_reason=authorization.revoked_reason,
        superseded_by=superseded_by_public_id,
        execution_start=execution_start,
        created_at=authorization.created_at,
    )


# --- Experiment Evidence Binding (frozen Design Freeze §B/§AH) ---------------

if TYPE_CHECKING:  # pragma: no cover - annotation only; the service owns the dataclass
    from app.strategy.experiment_evidence_claim_service import EvidenceClaimView

EVIDENCE_CLAIM_SEMANTICS = "PROVENANCE CLAIM ONLY — NOT ELIGIBILITY OR VALIDATION"
EVIDENCE_CLAIM_REPORTER_NOTE = (
    "claimed_by is the member who asserted this experiment association; "
    "it is not necessarily the member who originally reported the metric."
)


class CreateEvidenceClaimRequest(BaseModel):
    """Experiment Evidence Binding: exactly the four frozen inputs.
    ``extra="forbid"`` — no note, Variant, eligibility, assignment, exposure,
    result or validity field is ever accepted. ``metric_name`` is matched
    EXACTLY against the entry (no normalization), so it is not stripped."""

    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=1, max_length=EXPERIMENT_EVIDENCE_CLAIM_CLIENT_REQUEST_ID_MAX_LENGTH)
    required_signal_id: str = Field(min_length=1, max_length=20)
    metric_entry_id: str = Field(min_length=1, max_length=20)
    metric_name: str = Field(min_length=1, max_length=EXPERIMENT_EVIDENCE_CLAIM_METRIC_NAME_MAX_LENGTH)

    @field_validator("client_request_id", "required_signal_id", "metric_entry_id", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        return _strip_if_str(value)

    @field_validator("client_request_id", "required_signal_id", "metric_entry_id", "metric_name")
    @classmethod
    def _text_rules(cls, value: str) -> str:
        return _reject_nul(value)


class DisposeEvidenceClaimRequest(BaseModel):
    """Experiment Evidence Binding: exactly a required, non-blank ``reason``
    (pairs with the DB ``disposal_complete`` CHECK). No idempotency key."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=EXPERIMENT_EVIDENCE_CLAIM_DISPOSAL_REASON_MAX_LENGTH)

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value: Any) -> Any:
        return _strip_if_str(value)

    @field_validator("reason")
    @classmethod
    def _reason_rules(cls, value: str) -> str:
        return _reject_nul(value)


class EvidenceClaimAuthorizationRef(BaseModel):
    """Literal facts only: ``revoked_at``/``revoked_reason`` are exposed as
    stored and imply no eligibility (frozen R2)."""

    id: str
    revoked_at: datetime | None
    revoked_reason: str | None


class EvidenceClaimStartRef(BaseModel):
    """``started_at`` is the operator-ATTESTED instant, shown literally beside
    the datum period — no temporal comparison is made (frozen T1)."""

    id: str
    started_at: datetime


class EvidenceClaimSignalRef(BaseModel):
    """``tracking_required`` is declarative only — never a claim that tracking
    is implemented or valid."""

    id: str
    name: str
    expected_direction: str | None
    tracking_required: bool
    contract_version_id: str
    contract_version: int | None


class EvidenceClaimDatum(BaseModel):
    metric_entry_id: str
    metric_name: str
    value: Decimal | None
    period_start: date | None
    period_end: date | None
    channel: str | None
    source: MetricSource | None
    entry_created_at: datetime | None


class EvidenceClaimDistributionContext(BaseModel):
    """Present only when the datum's MetricEntry is owned by a
    ``DistributionMetricEvidence`` row. Such entries are excluded from the
    aggregate listing and the analysis pipeline by design (MVP-19B §37/§39)."""

    distribution_id: str | None
    evidence_id: str
    source_reference: str | None
    supersedes_evidence_id: str | None
    superseded_by_evidence_id: str | None
    correction_reason: str | None


class EvidenceClaimPublic(BaseModel):
    """ONE evidence claim. Public ids only. ``semantics``/``scope``/
    ``reporter_note`` are constants. ``later_correction_exists`` is a read-time
    observation — never stored, never a status, never valid/invalid. No
    eligibility/validity/variant/assignment/exposure/result/winner field exists."""

    id: str
    experiment_id: str
    semantics: Literal["PROVENANCE CLAIM ONLY — NOT ELIGIBILITY OR VALIDATION"] = EVIDENCE_CLAIM_SEMANTICS
    scope: Literal["EXPERIMENT_LEVEL"] = "EXPERIMENT_LEVEL"
    reporter_note: str = EVIDENCE_CLAIM_REPORTER_NOTE
    claimed_by: str | None
    created_at: datetime
    is_disposed: bool
    disposed_at: datetime | None
    disposed_by: str | None
    disposal_reason: str | None
    authorization: EvidenceClaimAuthorizationRef
    start: EvidenceClaimStartRef
    required_signal: EvidenceClaimSignalRef | None
    datum: EvidenceClaimDatum
    distribution: EvidenceClaimDistributionContext | None
    later_correction_exists: bool
    excluded_from_aggregate_and_analysis: bool


class EvidenceClaimListResponse(BaseModel):
    experiment_id: str
    start_id: str
    claims: list[EvidenceClaimPublic]


def evidence_claim_to_public(view: "EvidenceClaimView", *, experiment_public_id: str) -> EvidenceClaimPublic:
    claim = view.claim
    entry = view.entry
    signal = view.signal
    evidence = view.evidence
    return EvidenceClaimPublic(
        id=claim.public_id,
        experiment_id=experiment_public_id,
        claimed_by=view.claimed_by_public_id,
        created_at=claim.created_at,
        is_disposed=claim.disposed_at is not None,
        disposed_at=claim.disposed_at,
        disposed_by=view.disposed_by_public_id,
        disposal_reason=claim.disposal_reason,
        authorization=EvidenceClaimAuthorizationRef(
            id=view.authorization.public_id,
            revoked_at=view.authorization.revoked_at,
            revoked_reason=view.authorization.revoked_reason,
        ),
        start=EvidenceClaimStartRef(id=view.start.public_id, started_at=view.start.started_at),
        required_signal=(
            EvidenceClaimSignalRef(
                id=signal.public_id,
                name=signal.name,
                expected_direction=signal.expected_direction,
                tracking_required=signal.tracking_required,
                contract_version_id=view.contract_version.public_id if view.contract_version is not None else "",
                contract_version=view.contract_version.version if view.contract_version is not None else None,
            )
            if signal is not None
            else None
        ),
        datum=EvidenceClaimDatum(
            metric_entry_id=entry.public_id if entry is not None else "",
            metric_name=claim.metric_name,
            value=view.value,
            period_start=entry.period_start if entry is not None else None,
            period_end=entry.period_end if entry is not None else None,
            channel=entry.channel if entry is not None else None,
            source=entry.source if entry is not None else None,
            entry_created_at=entry.created_at if entry is not None else None,
        ),
        distribution=(
            EvidenceClaimDistributionContext(
                distribution_id=view.distribution.public_id if view.distribution is not None else None,
                evidence_id=evidence.public_id,
                source_reference=evidence.source_reference,
                supersedes_evidence_id=(
                    view.supersedes_evidence.public_id if view.supersedes_evidence is not None else None
                ),
                superseded_by_evidence_id=(
                    view.superseded_by_evidence.public_id if view.superseded_by_evidence is not None else None
                ),
                correction_reason=evidence.correction_reason,
            )
            if evidence is not None
            else None
        ),
        later_correction_exists=view.later_correction_exists,
        excluded_from_aggregate_and_analysis=evidence is not None,
    )
