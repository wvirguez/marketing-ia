"""Public DTOs for the strategy read surface. Never expose an internal
UUID or a raw ``workspace_id`` — only public_id-derived fields (BACKEND-08
§19, mirroring ``app/research/schemas.py``).

No field here ever represents approval, maturity, a Strategic Decision, or
a chain-of-thought/reasoning trace — see ``app/strategy/models.py`` for why
none of those exist on the underlying models in the first place.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.strategy.models import (
    EXPERIMENT_DEFINITION_CLIENT_REQUEST_ID_MAX_LENGTH,
    EXPERIMENT_DEFINITION_FACTOR_MAX_LENGTH,
    EXPERIMENT_DEFINITION_MAX_CONTROLLED_FACTORS,
    EXPERIMENT_DEFINITION_PROSE_MAX_LENGTH,
    EXPERIMENT_VARIANT_DESCRIPTION_MAX_LENGTH,
    EXPERIMENT_VARIANT_LABEL_MAX_LENGTH,
    ComparisonType,
    Experiment,
    ExperimentDefinitionVersion,
    ExperimentVariant,
    Hypothesis,
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
    is_pinned: bool = False


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
    pin_state: tuple[int, bool] = (0, False),
) -> ExperimentDefinitionPublic:
    """``pin_state`` is ``(variant_count, is_pinned)`` — derived by
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
    )


def experiment_to_public(
    experiment: Experiment,
    *,
    hypothesis_public_id: str,
    definition: ExperimentDefinitionVersion | None = None,
    definition_pin_state: tuple[int, bool] = (0, False),
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
