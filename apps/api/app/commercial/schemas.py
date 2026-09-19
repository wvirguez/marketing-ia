"""Public DTOs for the Commercial read/write surface. Never expose an
internal UUID, a raw ``workspace_id``, or a raw campaign UUID — only
public_id-derived fields and cross-references (mirroring
``app/learning/schemas.py``'s own convention exactly).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.commercial.models import (
    CommercialObjective,
    CommercialOutcome,
    Offer,
    _OUTCOME_CLIENT_REQUEST_ID_MAX_LENGTH,
    _OUTCOME_EXTERNAL_REFERENCE_MAX_LENGTH,
    _OUTCOME_MONETARY_VALUE_PRECISION,
    _OUTCOME_MONETARY_VALUE_SCALE,
    _OUTCOME_TYPE_MAX_LENGTH,
)

_CURRENCY_PATTERN = r"^[A-Z]{3}$"
_CORRECTION_REASON_MAX_LENGTH = 2000
# MVP-36B-R1: a request must never carry a value PostgreSQL would round
# (scale > 4) or overflow (Numeric(12,4) / int4) — rejected with 422 here,
# never left to the database.
_OUTCOME_QUANTITY_MAX = 2_147_483_647  # PostgreSQL int4 maximum
_OUTCOME_MONETARY_VALUE_MAX = Decimal(10) ** (
    _OUTCOME_MONETARY_VALUE_PRECISION - _OUTCOME_MONETARY_VALUE_SCALE
) - Decimal(10) ** -_OUTCOME_MONETARY_VALUE_SCALE  # 99999999.9999


# MVP-36B-R2/R3: ``occurred_at`` must stay decodable, not merely storable.
# The row itself is a valid PostgreSQL ``timestamptz``, but PostgreSQL renders
# it in the connection's SESSION timezone before psycopg finishes building the
# Python ``datetime``; a rendered local time in year 0 (BC) or year 10000
# makes psycopg raise ``DataError`` on EVERY later read (campaign GET,
# idempotent replay) — a request accepted with 201 would leave an unreadable
# row behind. The application never fixes the session timezone (it inherits
# the server/role/PGTZ default), so correctness cannot rely on UTC, and it
# cannot rely on psycopg's handling of unrecognised zones either: psycopg
# falls back to a UTC ``tzinfo`` for them, but the local time PostgreSQL has
# ALREADY rendered is unaffected by that fallback.
# So the accepted UTC interval reserves enough margin, at each end, for the
# largest session offset PostgreSQL will accept. Measured on PostgreSQL 14.24
# with psycopg 3.3.5 (re-derived at test time by
# ``tests/test_commercial_outcome_temporal.py``, which fails if it grows):
#   * named zones: at most 15.94h (Asia/Manila's pre-1844 LMT);
#   * plain numeric offsets: up to +/-167:59:59 (``SET TIME ZONE INTERVAL`` /
#     numeric form; string forms take whole minutes, up to 167:59), 168:00:00
#     is rejected;
#   * a POSIX zone with a DST rule and NO explicit DST offset implies
#     standard + 1h and PostgreSQL does not validate it, so the effective
#     rendered offset reaches +168:59:00 — the true maximum.
# The exact minimum reserve is therefore 167:59:59 below the lower end and
# 168:59:00 above the upper end (a plain seven-day margin is NOT enough at
# the upper end: 9999-12-24T23:59:59.999999Z overflows under such a zone).
# Eight whole calendar days (192h) are reserved at each end: deliberately
# conservative, simple to state, and harmless because instants within a week
# of year 1 or year 9999 have no meaning for a commercial event.
# The check is on the UTC instant, so an offset-bearing input such as
# ``0001-01-09T00:00:00+14:00`` (a UTC instant before the range) is rejected.
_OCCURRED_AT_MIN = datetime(1, 1, 9, tzinfo=timezone.utc)
_OCCURRED_AT_MAX = datetime(9999, 12, 23, 23, 59, 59, 999999, tzinfo=timezone.utc)


def _require_decodable_occurred_at(value: datetime) -> datetime:
    # Aware datetimes compare by instant (no UTC conversion, so no overflow).
    if value < _OCCURRED_AT_MIN or value > _OCCURRED_AT_MAX:
        raise ValueError(
            "occurred_at must be between 0001-01-09T00:00:00Z and 9999-12-23T23:59:59.999999Z (UTC instant)"
        )
    return value


OccurredAt = Annotated[AwareDatetime, AfterValidator(_require_decodable_occurred_at)]


def _reject_excess_monetary_scale(value: Decimal | None) -> Decimal | None:
    """MVP-36B-R1 (D2): the scale is judged as written — pydantic's own
    ``decimal_places`` ignores trailing zeros, so ``50.00000`` would slip
    through. A value is only ever accepted if it is written with at most
    ``Numeric(12,4)``'s own scale, so the persisted value never differs
    from (or is rounded relative to) the value the client sent."""
    if value is not None and value.as_tuple().exponent < -_OUTCOME_MONETARY_VALUE_SCALE:
        raise ValueError(f"monetary_value must have at most {_OUTCOME_MONETARY_VALUE_SCALE} decimal places")
    return value


class CommercialObjectivePublic(BaseModel):
    """MVP-27A-R1: 0..N simultaneously-current per Campaign — no PRIMARY,
    no version, no target field. ``current`` is derived, never a second
    persisted status column."""

    id: str
    campaign_id: str
    statement: str
    created_at: datetime
    current: bool
    superseded_at: datetime | None
    superseded_by_commercial_objective_id: str | None


class OfferPublic(BaseModel):
    """MVP-27A-R1/-R2: 0..N simultaneously-current per Campaign. ``price``/
    ``currency`` are both-null (unknown/undetermined/variable) or
    both-non-null (a known price, possibly zero/free) — never one alone."""

    id: str
    campaign_id: str
    statement: str
    price: Decimal | None
    currency: str | None
    created_at: datetime
    current: bool
    superseded_at: datetime | None
    superseded_by_offer_id: str | None


class CreateCommercialObjectiveRequest(BaseModel):
    """The sole writable field for a new/replacement CommercialObjective
    — no client-supplied ``status``, ``campaign_id``, ``workspace_id``,
    or supersession linkage; all of those are derived from the URL/
    session/target row (mirrors ``CreateStrategicImplicationRequest``
    exactly)."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=4000)


class CreateOfferRequest(BaseModel):
    """The writable fields for a new/replacement Offer. ``price``/
    ``currency`` must both be present or both be absent (MVP-27A-R1 §K,
    MVP-27A-R2 §E) — enforced here, before persistence, in addition to
    the database CheckConstraint (defense-in-depth, never the sole
    enforcement point)."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=4000)
    price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=_CURRENCY_PATTERN)

    @field_validator("currency", mode="before")
    @classmethod
    def _normalize_currency(cls, value: object) -> object:
        """Strip + uppercase before shape validation (MVP-27A-R2 §F) —
        "usd"/"  USD  " normalize to "USD"; malformed shapes (wrong
        length, non-alphabetic, internal whitespace) are rejected by the
        ``pattern`` constraint below, never silently coerced further. No
        ISO-4217 catalog is consulted — shape only."""
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @model_validator(mode="after")
    def _validate_price_currency_pair(self) -> "CreateOfferRequest":
        if (self.price is None) != (self.currency is None):
            raise ValueError("price and currency must both be provided or both be omitted")
        return self


def commercial_objective_to_public(
    objective: CommercialObjective, *, campaign_public_id: str, superseded_by_public_id: str | None = None
) -> CommercialObjectivePublic:
    return CommercialObjectivePublic(
        id=objective.public_id,
        campaign_id=campaign_public_id,
        statement=objective.statement,
        created_at=objective.created_at,
        current=objective.superseded_at is None,
        superseded_at=objective.superseded_at,
        superseded_by_commercial_objective_id=superseded_by_public_id,
    )


def offer_to_public(offer: Offer, *, campaign_public_id: str, superseded_by_public_id: str | None = None) -> OfferPublic:
    return OfferPublic(
        id=offer.public_id,
        campaign_id=campaign_public_id,
        statement=offer.statement,
        price=offer.price,
        currency=offer.currency,
        created_at=offer.created_at,
        current=offer.superseded_at is None,
        superseded_at=offer.superseded_at,
        superseded_by_offer_id=superseded_by_public_id,
    )


# --- CommercialOutcome (MVP-36, frozen by MVP-36A/-R1) -----------------------


class CommercialOutcomePublic(BaseModel):
    """MVP-36A §H/§AA + MVP-36A-R1 §12: EVENT semantics only — one row is
    one realized business event, never a period rollup. ``is_current`` is
    derived server-side (no other row's ``supersedes_outcome_id`` points
    at this row), never a second persisted status.
    ``content_distribution_id`` means ONLY observational association,
    never attribution/causality (the Attribution Firewall, MVP-36A §G).
    No ``experiment_id``/``variant_id``/``commercial_objective_id``/
    ``offer_id``/``tracking_requirement_id``/causal/success/winner field
    exists here, or ever will under this frozen contract."""

    id: str
    campaign_id: str
    content_distribution_id: str | None
    outcome_type: str
    quantity: int | None
    monetary_value: Decimal | None
    currency: str | None
    occurred_at: datetime
    created_at: datetime
    external_reference: str | None
    is_current: bool
    supersedes_outcome_id: str | None
    corrected_by_commercial_outcome_id: str | None
    correction_reason: str | None


class CreateCommercialOutcomeRequest(BaseModel):
    """MVP-36A §J/§AA: the writable fields for a new CommercialOutcome.
    No ``campaign_id``/``workspace_id``/``actor_user_id`` — all derived
    from the URL/session. ``content_distribution_id`` is the ONLY
    provenance field, optional, resolved campaign-scoped server-side
    (MVP-36A §5/§R). ``client_request_id`` is required — idempotency is
    never optional for this route (MVP-36A-R1 §3/§4)."""

    model_config = ConfigDict(extra="forbid")

    outcome_type: str = Field(min_length=1, max_length=_OUTCOME_TYPE_MAX_LENGTH)
    quantity: int | None = Field(default=None, gt=0, le=_OUTCOME_QUANTITY_MAX)
    monetary_value: Decimal | None = Field(
        default=None,
        ge=0,
        le=_OUTCOME_MONETARY_VALUE_MAX,
        max_digits=_OUTCOME_MONETARY_VALUE_PRECISION,
        decimal_places=_OUTCOME_MONETARY_VALUE_SCALE,
    )
    currency: str | None = Field(default=None, pattern=_CURRENCY_PATTERN)
    occurred_at: OccurredAt
    content_distribution_id: str | None = None
    external_reference: str | None = Field(default=None, max_length=_OUTCOME_EXTERNAL_REFERENCE_MAX_LENGTH)
    client_request_id: str = Field(min_length=1, max_length=_OUTCOME_CLIENT_REQUEST_ID_MAX_LENGTH)

    @field_validator("outcome_type", mode="before")
    @classmethod
    def _strip_outcome_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("outcome_type")
    @classmethod
    def _require_nonblank_outcome_type(cls, value: str) -> str:
        if not value:
            raise ValueError("outcome_type cannot be blank.")
        return value

    @field_validator("monetary_value")
    @classmethod
    def _validate_monetary_value_scale(cls, value: Decimal | None) -> Decimal | None:
        return _reject_excess_monetary_scale(value)

    @field_validator("currency", mode="before")
    @classmethod
    def _normalize_currency(cls, value: Any) -> Any:
        """Strip + uppercase before shape validation — mirrors
        ``CreateOfferRequest._normalize_currency`` exactly. No ISO-4217
        catalog is consulted — shape only."""
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @model_validator(mode="after")
    def _validate_monetary_value_currency_pair(self) -> "CreateCommercialOutcomeRequest":
        if (self.monetary_value is None) != (self.currency is None):
            raise ValueError("monetary_value and currency must both be provided or both be omitted")
        return self


class CorrectCommercialOutcomeRequest(BaseModel):
    """MVP-36A-R1 §9: FULL-STATE correction — the complete corrected
    claim, never a patch/diff. ``content_distribution_id`` is deliberately
    ABSENT from this schema (not merely forbidden by ``extra="forbid"``
    the way an unrecognized field would be, but never named as a field at
    all): Distribution provenance is structurally immutable through a
    correction chain, always inherited unconditionally from the target
    row. ``correction_reason`` is required — pairs with the model's own
    ``correction_reason_pairing`` CHECK constraint."""

    model_config = ConfigDict(extra="forbid")

    outcome_type: str = Field(min_length=1, max_length=_OUTCOME_TYPE_MAX_LENGTH)
    quantity: int | None = Field(default=None, gt=0, le=_OUTCOME_QUANTITY_MAX)
    monetary_value: Decimal | None = Field(
        default=None,
        ge=0,
        le=_OUTCOME_MONETARY_VALUE_MAX,
        max_digits=_OUTCOME_MONETARY_VALUE_PRECISION,
        decimal_places=_OUTCOME_MONETARY_VALUE_SCALE,
    )
    currency: str | None = Field(default=None, pattern=_CURRENCY_PATTERN)
    occurred_at: OccurredAt
    external_reference: str | None = Field(default=None, max_length=_OUTCOME_EXTERNAL_REFERENCE_MAX_LENGTH)
    client_request_id: str = Field(min_length=1, max_length=_OUTCOME_CLIENT_REQUEST_ID_MAX_LENGTH)
    correction_reason: str = Field(min_length=1, max_length=_CORRECTION_REASON_MAX_LENGTH)

    @field_validator("outcome_type", mode="before")
    @classmethod
    def _strip_outcome_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("outcome_type")
    @classmethod
    def _require_nonblank_outcome_type(cls, value: str) -> str:
        if not value:
            raise ValueError("outcome_type cannot be blank.")
        return value

    @field_validator("correction_reason", mode="before")
    @classmethod
    def _strip_correction_reason(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("correction_reason")
    @classmethod
    def _require_nonblank_correction_reason(cls, value: str) -> str:
        if not value:
            raise ValueError("correction_reason cannot be blank.")
        return value

    @field_validator("monetary_value")
    @classmethod
    def _validate_monetary_value_scale(cls, value: Decimal | None) -> Decimal | None:
        return _reject_excess_monetary_scale(value)

    @field_validator("currency", mode="before")
    @classmethod
    def _normalize_currency(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @model_validator(mode="after")
    def _validate_monetary_value_currency_pair(self) -> "CorrectCommercialOutcomeRequest":
        if (self.monetary_value is None) != (self.currency is None):
            raise ValueError("monetary_value and currency must both be provided or both be omitted")
        return self


def commercial_outcome_to_public(
    outcome: CommercialOutcome,
    *,
    campaign_public_id: str,
    content_distribution_public_id: str | None,
    supersedes_public_id: str | None,
    corrected_by_public_id: str | None,
) -> CommercialOutcomePublic:
    return CommercialOutcomePublic(
        id=outcome.public_id,
        campaign_id=campaign_public_id,
        content_distribution_id=content_distribution_public_id,
        outcome_type=outcome.outcome_type,
        quantity=outcome.quantity,
        monetary_value=outcome.monetary_value,
        currency=outcome.currency,
        occurred_at=outcome.occurred_at,
        created_at=outcome.created_at,
        external_reference=outcome.external_reference,
        is_current=corrected_by_public_id is None,
        supersedes_outcome_id=supersedes_public_id,
        corrected_by_commercial_outcome_id=corrected_by_public_id,
        correction_reason=outcome.correction_reason,
    )
