"""Money integrity tests for Offer.price/currency (MVP-27A-R1/-R2).
DB-level CheckConstraint tests are marked `postgres`; Pydantic
request-schema tests need no database at all.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.commercial.models import Offer
from app.commercial.schemas import CreateOfferRequest
from app.commercial.service import CommercialService
from tests.commercialtest import build_campaign, build_offer


# --- DB CheckConstraints (defense-in-depth, bypassing the schema) --------


@pytest.mark.postgres
def test_price_without_currency_violates_the_pair_constraint(db_session) -> None:
    campaign = build_campaign(db_session)
    db_session.add(Offer(public_id="OFR-TESTPAIR001", workspace_id=campaign.workspace_id, campaign_id=campaign.id,
                          statement="x", price=Decimal("10.00"), currency=None))
    with pytest.raises(IntegrityError) as error:
        db_session.commit()
    assert error.value.orig.diag.constraint_name == "ck_offers_price_currency_pair"
    db_session.rollback()


@pytest.mark.postgres
def test_currency_without_price_violates_the_pair_constraint(db_session) -> None:
    campaign = build_campaign(db_session)
    db_session.add(Offer(public_id="OFR-TESTPAIR002", workspace_id=campaign.workspace_id, campaign_id=campaign.id,
                          statement="x", price=None, currency="USD"))
    with pytest.raises(IntegrityError) as error:
        db_session.commit()
    assert error.value.orig.diag.constraint_name == "ck_offers_price_currency_pair"
    db_session.rollback()


@pytest.mark.postgres
def test_negative_price_violates_the_non_negative_constraint(db_session) -> None:
    campaign = build_campaign(db_session)
    db_session.add(Offer(public_id="OFR-TESTNEG001", workspace_id=campaign.workspace_id, campaign_id=campaign.id,
                          statement="x", price=Decimal("-1.00"), currency="USD"))
    with pytest.raises(IntegrityError) as error:
        db_session.commit()
    assert error.value.orig.diag.constraint_name == "ck_offers_price_non_negative"
    db_session.rollback()


@pytest.mark.postgres
def test_null_price_and_currency_is_valid(db_session) -> None:
    campaign, offer = build_offer(db_session, price=None, currency=None)
    assert offer.price is None and offer.currency is None


@pytest.mark.postgres
def test_free_offer_zero_price_with_currency_is_valid(db_session) -> None:
    campaign, offer = build_offer(db_session, price=Decimal("0"), currency="USD")
    assert offer.price == Decimal("0")
    assert offer.currency == "USD"


@pytest.mark.postgres
def test_four_decimal_price_survives_persistence_exactly(db_session) -> None:
    campaign, offer = build_offer(db_session, price=Decimal("199.9999"), currency="USD")
    db_session.commit()
    db_session.expire_all()
    reloaded = CommercialService(db_session).offers.get_by_id(offer.id)
    assert reloaded.price == Decimal("199.9999")
    assert isinstance(reloaded.price, Decimal)


# --- Pydantic request-schema validation (no database needed) ------------


def test_schema_accepts_both_null_price_and_currency() -> None:
    payload = CreateOfferRequest(statement="x", price=None, currency=None)
    assert payload.price is None and payload.currency is None


def test_schema_accepts_matched_price_and_currency() -> None:
    payload = CreateOfferRequest(statement="x", price=Decimal("10.00"), currency="USD")
    assert payload.price == Decimal("10.00")
    assert payload.currency == "USD"


def test_schema_rejects_price_only() -> None:
    with pytest.raises(ValidationError):
        CreateOfferRequest(statement="x", price=Decimal("10.00"), currency=None)


def test_schema_rejects_currency_only() -> None:
    with pytest.raises(ValidationError):
        CreateOfferRequest(statement="x", price=None, currency="USD")


def test_schema_rejects_negative_price() -> None:
    with pytest.raises(ValidationError):
        CreateOfferRequest(statement="x", price=Decimal("-1.00"), currency="USD")


def test_schema_accepts_zero_price_as_free() -> None:
    payload = CreateOfferRequest(statement="x", price=Decimal("0"), currency="USD")
    assert payload.price == Decimal("0")


@pytest.mark.parametrize("raw,expected", [("usd", "USD"), ("  USD  ", "USD"), ("Usd", "USD")])
def test_schema_normalizes_currency_case_and_whitespace(raw, expected) -> None:
    payload = CreateOfferRequest(statement="x", price=Decimal("1.00"), currency=raw)
    assert payload.currency == expected


@pytest.mark.parametrize("raw", ["U", "US", "USDD", "123", "U$D", "U D", ""])
def test_schema_rejects_malformed_currency_shapes(raw) -> None:
    with pytest.raises(ValidationError):
        CreateOfferRequest(statement="x", price=Decimal("1.00"), currency=raw)


def test_schema_does_not_claim_iso_4217_validity() -> None:
    """Shape-only: a well-formed-but-nonexistent code is accepted — no
    currency catalog is consulted (MVP-27A-R2 §F)."""
    payload = CreateOfferRequest(statement="x", price=Decimal("1.00"), currency="ZZZ")
    assert payload.currency == "ZZZ"
