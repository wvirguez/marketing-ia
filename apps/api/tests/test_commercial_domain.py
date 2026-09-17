"""Domain/service-level tests for CommercialObjective / Offer (MVP-27).
All marked `postgres` — these exercise ``CommercialService`` directly
against real domain objects, the same pattern established throughout
``tests/test_tracking_domain.py``/``tests/test_learning_domain.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.campaigns.repository import CampaignBriefRepository
from app.commercial.repository import CommercialObjectiveRepository, OfferRepository
from app.commercial.service import CommercialService
from app.core.api_errors import CommercialObjectiveAlreadySupersededError, ForbiddenError, OfferAlreadySupersededError
from tests.commercialtest import build_campaign, build_campaign_with_brief, build_commercial_objective, build_offer

pytestmark = pytest.mark.postgres


# --- independent creation: 0..N simultaneously current ------------------


def test_two_independent_objective_creates_both_succeed_as_distinct_current_rows(db_session) -> None:
    campaign = build_campaign(db_session)
    service = CommercialService(db_session)
    first = service.record_commercial_objective(campaign=campaign, statement="Generate qualified leads.")
    second = service.record_commercial_objective(campaign=campaign, statement="Generate revenue.")
    assert first.id != second.id
    assert first.superseded_at is None and second.superseded_at is None
    rows = service.list_objectives_for_campaign(campaign.id)
    assert {r.id for r in rows} == {first.id, second.id}


def test_two_independent_offer_creates_both_succeed_as_distinct_current_rows(db_session) -> None:
    campaign = build_campaign(db_session)
    service = CommercialService(db_session)
    first = service.record_offer(campaign=campaign, statement="Course only.", price=Decimal("199.00"), currency="USD")
    second = service.record_offer(campaign=campaign, statement="Course + coaching.", price=Decimal("299.00"), currency="USD")
    assert first.id != second.id
    assert first.superseded_at is None and second.superseded_at is None
    rows = service.list_offers_for_campaign(campaign.id)
    assert {r.id for r in rows} == {first.id, second.id}


# --- supersession: atomic, one-shot --------------------------------------


def test_supersede_objective_creates_replacement_and_marks_original_historical(db_session) -> None:
    campaign, original = build_commercial_objective(db_session)
    service = CommercialService(db_session)
    replacement = service.supersede_commercial_objective(
        campaign=campaign, objective_public_id=original.public_id, statement="Generate revenue instead."
    )
    db_session.refresh(original)
    assert replacement.id != original.id
    assert replacement.superseded_at is None  # replacement is current
    assert original.superseded_at is not None
    assert original.superseded_by_commercial_objective_id == replacement.id
    assert replacement.campaign_id == original.campaign_id
    assert replacement.workspace_id == original.workspace_id


def test_supersede_offer_creates_replacement_and_marks_original_historical(db_session) -> None:
    campaign, original = build_offer(db_session, price=Decimal("199.00"), currency="USD")
    service = CommercialService(db_session)
    replacement = service.supersede_offer(
        campaign=campaign, offer_public_id=original.public_id, statement="Course, new price.",
        price=Decimal("249.00"), currency="USD",
    )
    db_session.refresh(original)
    assert replacement.id != original.id
    assert replacement.superseded_at is None
    assert original.superseded_at is not None
    assert original.superseded_by_offer_id == replacement.id
    assert replacement.campaign_id == original.campaign_id
    assert replacement.workspace_id == original.workspace_id


def test_second_supersede_attempt_on_the_same_objective_is_a_deterministic_conflict(db_session) -> None:
    campaign, original = build_commercial_objective(db_session)
    service = CommercialService(db_session)
    service.supersede_commercial_objective(
        campaign=campaign, objective_public_id=original.public_id, statement="First replacement."
    )
    with pytest.raises(CommercialObjectiveAlreadySupersededError):
        service.supersede_commercial_objective(
            campaign=campaign, objective_public_id=original.public_id, statement="Second replacement attempt."
        )
    rows = service.list_objectives_for_campaign(campaign.id)
    assert len(rows) == 2  # original + exactly one replacement, never two


def test_second_supersede_attempt_on_the_same_offer_is_a_deterministic_conflict(db_session) -> None:
    campaign, original = build_offer(db_session)
    service = CommercialService(db_session)
    service.supersede_offer(campaign=campaign, offer_public_id=original.public_id, statement="First replacement.")
    with pytest.raises(OfferAlreadySupersededError):
        service.supersede_offer(campaign=campaign, offer_public_id=original.public_id, statement="Second replacement attempt.")
    rows = service.list_offers_for_campaign(campaign.id)
    assert len(rows) == 2


def test_superseding_an_already_superseded_row_a_second_time_via_its_replacement_is_a_new_independent_chain(db_session) -> None:
    """Superseding the *replacement* itself is legal (it is still
    current) and produces a third row — a chain, never a cycle back to
    the original (structurally impossible by construction: the
    replacement is always freshly created)."""
    campaign, original = build_commercial_objective(db_session)
    service = CommercialService(db_session)
    replacement = service.supersede_commercial_objective(
        campaign=campaign, objective_public_id=original.public_id, statement="Second version."
    )
    third = service.supersede_commercial_objective(
        campaign=campaign, objective_public_id=replacement.public_id, statement="Third version."
    )
    db_session.refresh(replacement)
    assert third.superseded_at is None
    assert replacement.superseded_by_commercial_objective_id == third.id
    assert third.superseded_by_commercial_objective_id != original.id


# --- tenancy: non-leaky campaign-scoped resolution -----------------------


def test_objective_lookup_is_scoped_to_its_own_campaign(db_session) -> None:
    campaign_a, objective = build_commercial_objective(db_session, campaign_name="Campaign A")
    campaign_b = build_campaign(db_session, org_name="Other Org", workspace_name="Other WS", campaign_name="Campaign B")
    found_in_own_campaign = CommercialObjectiveRepository(db_session).get_for_campaign_by_public_id(
        campaign_id=campaign_a.id, public_id=objective.public_id
    )
    found_in_other_campaign = CommercialObjectiveRepository(db_session).get_for_campaign_by_public_id(
        campaign_id=campaign_b.id, public_id=objective.public_id
    )
    assert found_in_own_campaign is not None and found_in_own_campaign.id == objective.id
    assert found_in_other_campaign is None


def test_offer_lookup_is_scoped_to_its_own_campaign(db_session) -> None:
    campaign_a, offer = build_offer(db_session, campaign_name="Campaign A")
    campaign_b = build_campaign(db_session, org_name="Other Org 2", workspace_name="Other WS 2", campaign_name="Campaign B")
    found_in_own_campaign = OfferRepository(db_session).get_for_campaign_by_public_id(
        campaign_id=campaign_a.id, public_id=offer.public_id
    )
    found_in_other_campaign = OfferRepository(db_session).get_for_campaign_by_public_id(
        campaign_id=campaign_b.id, public_id=offer.public_id
    )
    assert found_in_own_campaign is not None and found_in_own_campaign.id == offer.id
    assert found_in_other_campaign is None


def test_supersede_of_nonexistent_objective_is_forbidden_not_a_bare_404(db_session) -> None:
    campaign = build_campaign(db_session)
    service = CommercialService(db_session)
    with pytest.raises(ForbiddenError):
        service.supersede_commercial_objective(campaign=campaign, objective_public_id="OBJ-DOESNOTEXIST", statement="x")


def test_supersede_of_nonexistent_offer_is_forbidden(db_session) -> None:
    campaign = build_campaign(db_session)
    service = CommercialService(db_session)
    with pytest.raises(ForbiddenError):
        service.supersede_offer(campaign=campaign, offer_public_id="OFR-DOESNOTEXIST", statement="x")


# --- immutability ---------------------------------------------------------


def test_no_update_or_delete_method_exists_for_objective_or_offer_repositories() -> None:
    for repository_cls in (CommercialObjectiveRepository, OfferRepository):
        assert not hasattr(repository_cls, "update")
        assert not hasattr(repository_cls, "delete")


def test_statement_is_never_mutated_in_place_by_supersession(db_session) -> None:
    campaign, original = build_commercial_objective(db_session, statement="Original statement.")
    original_statement = original.statement
    service = CommercialService(db_session)
    service.supersede_commercial_objective(
        campaign=campaign, objective_public_id=original.public_id, statement="Replacement statement."
    )
    db_session.refresh(original)
    assert original.statement == original_statement  # never rewritten in place


# --- CampaignBrief boundary (MVP-27A-R1 §I) --------------------------------


def test_recording_an_offer_never_mutates_campaignbrief(db_session) -> None:
    campaign, brief_before = build_campaign_with_brief(db_session)
    CommercialService(db_session).record_offer(
        campaign=campaign, statement="A new offer.", price=Decimal("50.00"), currency="USD"
    )
    brief_after = CampaignBriefRepository(db_session).get_latest_for_campaign(campaign.id)
    assert brief_after.price == brief_before.price
    assert brief_after.product_type == brief_before.product_type
    assert brief_after.version == brief_before.version


def test_recording_an_objective_never_mutates_campaignbrief(db_session) -> None:
    campaign, brief_before = build_campaign_with_brief(db_session)
    CommercialService(db_session).record_commercial_objective(campaign=campaign, statement="Generate leads.")
    brief_after = CampaignBriefRepository(db_session).get_latest_for_campaign(campaign.id)
    assert brief_after.price == brief_before.price
    assert brief_after.version == brief_before.version


def test_supersession_never_mutates_campaignbrief(db_session) -> None:
    campaign, brief_before = build_campaign_with_brief(db_session)
    service = CommercialService(db_session)
    original = service.record_commercial_objective(campaign=campaign, statement="Generate leads.")
    service.supersede_commercial_objective(
        campaign=campaign, objective_public_id=original.public_id, statement="Generate revenue."
    )
    brief_after = CampaignBriefRepository(db_session).get_latest_for_campaign(campaign.id)
    assert brief_after.price == brief_before.price
    assert brief_after.version == brief_before.version


def test_campaign_with_no_commercial_entities_remains_fully_readable(db_session) -> None:
    campaign = build_campaign(db_session)
    service = CommercialService(db_session)
    assert service.list_objectives_for_campaign(campaign.id) == []
    assert service.list_offers_for_campaign(campaign.id) == []


# --- archived Campaign policy (MVP-27A-R1 §L) ------------------------------


def test_creation_is_permitted_on_an_archived_campaign(db_session) -> None:
    campaign = build_campaign(db_session)
    campaign.archived_at = datetime.now(timezone.utc)
    db_session.commit()
    service = CommercialService(db_session)
    objective = service.record_commercial_objective(campaign=campaign, statement="Still allowed.")
    offer = service.record_offer(campaign=campaign, statement="Still allowed.")
    assert objective.id is not None
    assert offer.id is not None


def test_supersession_is_permitted_on_an_archived_campaign(db_session) -> None:
    campaign, original = build_commercial_objective(db_session)
    campaign.archived_at = datetime.now(timezone.utc)
    db_session.commit()
    service = CommercialService(db_session)
    replacement = service.supersede_commercial_objective(
        campaign=campaign, objective_public_id=original.public_id, statement="Still allowed after archive."
    )
    assert replacement.superseded_at is None
