"""Domain/service-level tests for StrategicDecision (MVP-28B, implementing
the frozen MVP-28A/-R1/-R2 contract). All marked `postgres` — these
exercise ``StrategicDecisionService`` directly against real domain
objects, the same pattern established throughout
``tests/test_commercial_domain.py``/``tests/test_learning_domain.py``.
"""

from __future__ import annotations

import pytest

from app.core.api_errors import (
    ForbiddenError,
    StrategicDecisionAlreadyExistsError,
    StrategicDecisionAlreadySupersededError,
    StrategicRecommendationNotAcceptedError,
)
from app.learning.models import StrategicRecommendationDecision
from app.learning.service import LearningService
from app.orchestration.models import StrategicDecisionType
from app.orchestration.repository import StrategicDecisionRepository
from app.orchestration.service import StrategicDecisionService
from tests.commercialtest import build_campaign
from tests.contenttest import make_user
from tests.learningtest import build_recommendation
from tests.orchestrationtest import build_accepted_recommendation, build_strategic_decision

pytestmark = pytest.mark.postgres


# --- origin gate: ACCEPTED Recommendation only (Model C) -------------------


def test_recording_a_decision_requires_an_accepted_recommendation(db_session) -> None:
    campaign, _analysis_result, _candidate, recommendation = build_recommendation(db_session)
    actor = make_user(db_session)
    db_session.commit()
    with pytest.raises(StrategicRecommendationNotAcceptedError):
        StrategicDecisionService(db_session).record_decision(
            campaign=campaign,
            recommendation_public_id=recommendation.public_id,
            decision_type=StrategicDecisionType.ADOPT,
            statement="Too early.",
            actor_user_id=actor.id,
        )


def test_recording_a_decision_against_a_rejected_recommendation_is_rejected(db_session) -> None:
    campaign, _analysis_result, _candidate, recommendation = build_recommendation(db_session)
    actor = make_user(db_session)
    db_session.commit()
    LearningService(db_session).decide_strategic_recommendation_candidate(
        campaign=campaign,
        recommendation_public_id=recommendation.public_id,
        decision=StrategicRecommendationDecision.REJECTED,
        actor_user_id=actor.id,
    )
    with pytest.raises(StrategicRecommendationNotAcceptedError):
        StrategicDecisionService(db_session).record_decision(
            campaign=campaign,
            recommendation_public_id=recommendation.public_id,
            decision_type=StrategicDecisionType.ADOPT,
            statement="Recommendation was rejected, not accepted.",
            actor_user_id=actor.id,
        )


def test_recording_a_decision_against_an_accepted_recommendation_succeeds(db_session) -> None:
    campaign, recommendation, actor = build_accepted_recommendation(db_session)
    decision = StrategicDecisionService(db_session).record_decision(
        campaign=campaign,
        recommendation_public_id=recommendation.public_id,
        decision_type=StrategicDecisionType.ADOPT,
        statement="Adopt the shorter-hooks direction.",
        actor_user_id=actor.id,
    )
    assert decision.strategic_recommendation_candidate_id == recommendation.id
    assert decision.decision_type is StrategicDecisionType.ADOPT
    assert decision.superseded_at is None
    assert decision.public_id.startswith("DEC-")


def test_recording_a_decision_never_mutates_the_recommendation(db_session) -> None:
    campaign, recommendation, actor = build_accepted_recommendation(db_session)
    decision_before = recommendation.decision
    decided_at_before = recommendation.decided_at
    StrategicDecisionService(db_session).record_decision(
        campaign=campaign,
        recommendation_public_id=recommendation.public_id,
        decision_type=StrategicDecisionType.DECLINE,
        statement="Declined despite acceptance.",
        actor_user_id=actor.id,
    )
    db_session.refresh(recommendation)
    assert recommendation.decision == decision_before  # still ACCEPTED, never rewritten
    assert recommendation.decided_at == decided_at_before


# --- current-decision invariant: at most one per Recommendation -----------


def test_recording_a_second_first_decision_for_the_same_recommendation_is_a_deterministic_conflict(db_session) -> None:
    campaign, recommendation, _decision, actor = build_strategic_decision(db_session)
    with pytest.raises(StrategicDecisionAlreadyExistsError):
        StrategicDecisionService(db_session).record_decision(
            campaign=campaign,
            recommendation_public_id=recommendation.public_id,
            decision_type=StrategicDecisionType.DEFER,
            statement="Second attempt without superseding.",
            actor_user_id=actor.id,
        )


def test_current_decision_query_returns_exactly_one_row(db_session) -> None:
    campaign, recommendation, decision, _actor = build_strategic_decision(db_session)
    current = StrategicDecisionRepository(db_session).get_current_for_recommendation(recommendation_id=recommendation.id)
    assert current is not None and current.id == decision.id


# --- supersession: atomic, one-shot, Model B (OLD -> NEW) -------------------


def test_supersede_decision_creates_replacement_and_marks_original_historical(db_session) -> None:
    campaign, recommendation, original, actor = build_strategic_decision(db_session, decision_type=StrategicDecisionType.ADOPT)
    replacement = StrategicDecisionService(db_session).supersede_decision(
        campaign=campaign,
        decision_public_id=original.public_id,
        decision_type=StrategicDecisionType.DEFER,
        statement="On reflection, defer instead.",
        actor_user_id=actor.id,
    )
    db_session.refresh(original)
    assert replacement.id != original.id
    assert replacement.superseded_at is None  # replacement is current
    assert replacement.strategic_recommendation_candidate_id == recommendation.id
    assert original.superseded_at is not None
    assert original.superseded_by_strategic_decision_id == replacement.id
    assert replacement.campaign_id == original.campaign_id
    assert replacement.workspace_id == original.workspace_id


def test_supersede_makes_replacement_the_current_decision_for_the_recommendation(db_session) -> None:
    campaign, recommendation, original, actor = build_strategic_decision(db_session)
    replacement = StrategicDecisionService(db_session).supersede_decision(
        campaign=campaign,
        decision_public_id=original.public_id,
        decision_type=StrategicDecisionType.DECLINE,
        statement="Now declining.",
        actor_user_id=actor.id,
    )
    current = StrategicDecisionRepository(db_session).get_current_for_recommendation(recommendation_id=recommendation.id)
    assert current is not None and current.id == replacement.id


def test_second_supersede_attempt_on_the_same_original_is_a_deterministic_conflict(db_session) -> None:
    campaign, _recommendation, original, actor = build_strategic_decision(db_session)
    service = StrategicDecisionService(db_session)
    service.supersede_decision(
        campaign=campaign, decision_public_id=original.public_id, decision_type=StrategicDecisionType.DEFER,
        statement="First replacement.", actor_user_id=actor.id,
    )
    with pytest.raises(StrategicDecisionAlreadySupersededError):
        service.supersede_decision(
            campaign=campaign, decision_public_id=original.public_id, decision_type=StrategicDecisionType.DECLINE,
            statement="Second replacement attempt against a stale original.", actor_user_id=actor.id,
        )


def test_superseding_a_replacement_a_second_time_is_a_new_independent_chain_not_a_cycle(db_session) -> None:
    """Superseding the *replacement* itself is legal (it is still current)
    and produces a third row — a chain, never a cycle back to the
    original (structurally impossible by construction: the replacement is
    always freshly created)."""
    campaign, _recommendation, original, actor = build_strategic_decision(db_session)
    service = StrategicDecisionService(db_session)
    replacement = service.supersede_decision(
        campaign=campaign, decision_public_id=original.public_id, decision_type=StrategicDecisionType.DEFER,
        statement="Second version.", actor_user_id=actor.id,
    )
    third = service.supersede_decision(
        campaign=campaign, decision_public_id=replacement.public_id, decision_type=StrategicDecisionType.ADOPT,
        statement="Third version.", actor_user_id=actor.id,
    )
    db_session.refresh(replacement)
    assert third.superseded_at is None
    assert replacement.superseded_by_strategic_decision_id == third.id
    assert third.superseded_by_strategic_decision_id != original.id


def test_supersede_of_nonexistent_decision_is_forbidden_not_a_bare_404(db_session) -> None:
    campaign = build_campaign(db_session)
    with pytest.raises(ForbiddenError):
        StrategicDecisionService(db_session).supersede_decision(
            campaign=campaign, decision_public_id="DEC-DOESNOTEXIST", decision_type=StrategicDecisionType.ADOPT,
            statement="x", actor_user_id=make_user(db_session).id,
        )


# --- tenancy: non-leaky campaign-scoped resolution -------------------------


def test_decision_lookup_is_scoped_to_its_own_campaign(db_session) -> None:
    campaign_a, _recommendation, decision, _actor = build_strategic_decision(db_session, campaign_name="Campaign A")
    campaign_b = build_campaign(db_session, org_name="Other Org", workspace_name="Other WS", campaign_name="Campaign B")
    repo = StrategicDecisionRepository(db_session)
    found_in_own_campaign = repo.get_for_campaign_by_public_id(campaign_id=campaign_a.id, public_id=decision.public_id)
    found_in_other_campaign = repo.get_for_campaign_by_public_id(campaign_id=campaign_b.id, public_id=decision.public_id)
    assert found_in_own_campaign is not None and found_in_own_campaign.id == decision.id
    assert found_in_other_campaign is None


def test_recording_a_decision_against_a_recommendation_from_a_different_campaign_is_forbidden(db_session) -> None:
    _campaign_a, recommendation, actor = build_accepted_recommendation(db_session, campaign_name="Campaign A")
    campaign_b = build_campaign(db_session, org_name="Other Org 2", workspace_name="Other WS 2", campaign_name="Campaign B")
    with pytest.raises(ForbiddenError):
        StrategicDecisionService(db_session).record_decision(
            campaign=campaign_b,
            recommendation_public_id=recommendation.public_id,
            decision_type=StrategicDecisionType.ADOPT,
            statement="Cross-campaign attempt.",
            actor_user_id=actor.id,
        )


# --- immutability -----------------------------------------------------------


def test_no_update_or_delete_method_exists_for_the_decision_repository() -> None:
    assert not hasattr(StrategicDecisionRepository, "update")
    assert not hasattr(StrategicDecisionRepository, "delete")


def test_statement_and_decision_type_are_never_mutated_in_place_by_supersession(db_session) -> None:
    campaign, _recommendation, original, actor = build_strategic_decision(
        db_session, decision_type=StrategicDecisionType.ADOPT, statement="Original statement."
    )
    original_statement = original.statement
    original_type = original.decision_type
    StrategicDecisionService(db_session).supersede_decision(
        campaign=campaign, decision_public_id=original.public_id, decision_type=StrategicDecisionType.DECLINE,
        statement="Replacement statement.", actor_user_id=actor.id,
    )
    db_session.refresh(original)
    assert original.statement == original_statement  # never rewritten in place
    assert original.decision_type == original_type


def test_superseded_decision_remains_independently_readable(db_session) -> None:
    campaign, _recommendation, original, actor = build_strategic_decision(db_session)
    StrategicDecisionService(db_session).supersede_decision(
        campaign=campaign, decision_public_id=original.public_id, decision_type=StrategicDecisionType.DEFER,
        statement="Replacement.", actor_user_id=actor.id,
    )
    reread = StrategicDecisionRepository(db_session).get_for_campaign_by_public_id(
        campaign_id=campaign.id, public_id=original.public_id
    )
    assert reread is not None
    assert reread.superseded_at is not None


# --- legacy compatibility (MVP-28A-R1 §Y / MVP-28B §21) ---------------------


def test_accepted_recommendation_without_a_decision_remains_valid_and_unbackfilled(db_session) -> None:
    campaign, recommendation, _actor = build_accepted_recommendation(db_session)
    current = StrategicDecisionRepository(db_session).get_current_for_recommendation(recommendation_id=recommendation.id)
    assert current is None  # no fabricated Decision
    db_session.refresh(recommendation)
    assert recommendation.decision is StrategicRecommendationDecision.ACCEPTED  # still fully valid/readable


def test_campaign_with_no_strategic_decisions_remains_fully_readable(db_session) -> None:
    campaign = build_campaign(db_session)
    assert StrategicDecisionService(db_session).list_decisions_for_campaign(campaign.id) == []
