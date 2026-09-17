"""Public DTOs for the Commercial read/write surface. Never expose an
internal UUID, a raw ``workspace_id``, or a raw campaign UUID — only
public_id-derived fields and cross-references (mirroring
``app/learning/schemas.py``'s own convention exactly).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.commercial.models import CommercialObjective, Offer

_CURRENCY_PATTERN = r"^[A-Z]{3}$"


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
