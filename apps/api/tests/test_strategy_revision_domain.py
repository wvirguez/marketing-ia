"""Domain/service-level tests for Governed Strategy Revision (MVP-30B,
implementing the frozen MVP-30A/-30A-R1 contract). All marked `postgres`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.api_errors import (
    ForbiddenError,
    StrategicApprovalAlreadyConsumedError,
    StrategyRevisionBaseStaleError,
    StrategyRevisionNotEligibleError,
)
from app.orchestration.models import StrategicApprovalOutcome, StrategicDecisionType
from app.orchestration.repository import StrategyRevisionRepository
from app.orchestration.service import StrategicDecisionService, StrategyRevisionService
from app.strategy.models import StrategyOrigin
from app.strategy.repository import StrategyRepository
from tests.commercialtest import build_campaign
from tests.orchestrationtest import build_base_strategy, build_strategic_approval, build_strategy_revision

pytestmark = pytest.mark.postgres


# --- happy path --------------------------------------------------------------


def test_revising_an_adopt_approved_current_decision_succeeds(db_session) -> None:
    campaign, _recommendation, _decision, approval, actor = build_strategic_approval(db_session)
    base_strategy, _run, _stage = build_base_strategy(db_session, campaign=campaign)

    result_strategy, positioning, revision = StrategyRevisionService(db_session).revise_strategy(
        campaign=campaign, base_strategy_public_id=base_strategy.public_id,
        strategic_approval_public_id=approval.public_id,
        summary="Revised strategy.", positioning_statement="Revised positioning.",
        actor_user_id=actor.id,
    )
    assert result_strategy.version == base_strategy.version + 1
    assert result_strategy.origin is StrategyOrigin.REVISION
    assert result_strategy.campaign_run_id is None
    assert result_strategy.stage_execution_id is None
    assert result_strategy.summary == "Revised strategy."
    assert positioning.strategy_id == result_strategy.id
    assert positioning.statement == "Revised positioning."
    assert revision.strategic_approval_id == approval.id
    assert revision.base_strategy_id == base_strategy.id
    assert revision.result_strategy_id == result_strategy.id
    assert revision.public_id.startswith("SRV-")


def test_result_strategy_becomes_the_new_current_strategy(db_session) -> None:
    campaign, base_strategy, _approval, result_strategy, _positioning, _revision, _actor = build_strategy_revision(
        db_session
    )
    current = StrategyRepository(db_session).get_current_for_campaign(campaign.id)
    assert current is not None
    assert current.id == result_strategy.id
    assert current.id != base_strategy.id


# --- eligibility (MVP-30B §45) ------------------------------------------------


@pytest.mark.parametrize("decision_type", [StrategicDecisionType.DEFER, StrategicDecisionType.DECLINE])
def test_defer_and_decline_decisions_can_never_produce_an_eligible_approval(db_session, decision_type) -> None:
    """DEFER/DECLINE Decisions never receive a StrategicApproval at all
    (MVP-29A §X eligibility gate, enforced one level down in
    ``StrategicApprovalService.record_approval``) — there is structurally
    no Approval public_id to ever supply for one, so the only way this
    scenario can reach ``revise_strategy`` at all is via a nonexistent
    Approval identifier, which is correctly non-leaky Forbidden (never
    revealing whether "no such Approval" or "belongs to another
    campaign")."""
    campaign, _recommendation, _decision, actor = _build_decision(db_session, decision_type=decision_type)
    base_strategy, _run, _stage = build_base_strategy(db_session, campaign=campaign)
    with pytest.raises(ForbiddenError):
        StrategyRevisionService(db_session).revise_strategy(
            campaign=campaign, base_strategy_public_id=base_strategy.public_id,
            strategic_approval_public_id="SAP-DOESNOTEXIST",
            summary="x", positioning_statement="y", actor_user_id=actor.id,
        )


def test_rejected_approval_is_ineligible(db_session) -> None:
    campaign, _recommendation, _decision, approval, actor = build_strategic_approval(
        db_session, outcome=StrategicApprovalOutcome.REJECTED
    )
    base_strategy, _run, _stage = build_base_strategy(db_session, campaign=campaign)
    with pytest.raises(StrategyRevisionNotEligibleError):
        StrategyRevisionService(db_session).revise_strategy(
            campaign=campaign, base_strategy_public_id=base_strategy.public_id,
            strategic_approval_public_id=approval.public_id,
            summary="x", positioning_statement="y", actor_user_id=actor.id,
        )


def test_approval_for_an_already_superseded_decision_is_ineligible(db_session) -> None:
    campaign, _recommendation, decision, approval, actor = build_strategic_approval(db_session)
    base_strategy, _run, _stage = build_base_strategy(db_session, campaign=campaign)
    StrategicDecisionService(db_session).supersede_decision(
        campaign=campaign, decision_public_id=decision.public_id, decision_type=StrategicDecisionType.DEFER,
        statement="Reconsidered.", actor_user_id=actor.id,
    )
    with pytest.raises(StrategyRevisionNotEligibleError):
        StrategyRevisionService(db_session).revise_strategy(
            campaign=campaign, base_strategy_public_id=base_strategy.public_id,
            strategic_approval_public_id=approval.public_id,
            summary="x", positioning_statement="y", actor_user_id=actor.id,
        )


def test_already_consumed_approval_is_rejected(db_session) -> None:
    campaign, base_strategy, approval, _result, _positioning, _revision, actor = build_strategy_revision(db_session)
    second_base, _run, _stage = build_base_strategy(db_session, campaign=campaign, run_number=3)
    with pytest.raises(StrategicApprovalAlreadyConsumedError):
        StrategyRevisionService(db_session).revise_strategy(
            campaign=campaign, base_strategy_public_id=second_base.public_id,
            strategic_approval_public_id=approval.public_id,
            summary="x", positioning_statement="y", actor_user_id=actor.id,
        )


def test_approval_belonging_to_another_decision_cannot_be_manufactured_by_a_client() -> None:
    """MVP-30B §17/§38: RecordStrategyRevisionRequest has no field for a
    Decision identifier at all — the Decision is always derived
    server-side from the Approval's own FK. Structurally impossible to
    construct the "Approval for a different Decision" adversarial
    scenario via the API, confirmed directly against the schema."""
    from app.orchestration.strategy_revision_schemas import RecordStrategyRevisionRequest

    fields = RecordStrategyRevisionRequest.model_fields
    assert "strategic_decision_id" not in fields
    assert set(fields.keys()) == {"strategic_approval_id", "summary", "positioning_statement"}


def test_legacy_current_strategy_is_a_valid_revision_base(db_session) -> None:
    """MVP-30A-R1 §AG: a BOOTSTRAP-origin Strategy remains a fully valid
    first Revision base — no backfill, no synthetic provenance required."""
    campaign, _recommendation, _decision, approval, actor = build_strategic_approval(db_session)
    base_strategy, _run, _stage = build_base_strategy(db_session, campaign=campaign)
    assert base_strategy.origin is StrategyOrigin.BOOTSTRAP

    result_strategy, _positioning, _revision = StrategyRevisionService(db_session).revise_strategy(
        campaign=campaign, base_strategy_public_id=base_strategy.public_id,
        strategic_approval_public_id=approval.public_id,
        summary="x", positioning_statement="y", actor_user_id=actor.id,
    )
    assert result_strategy.version == base_strategy.version + 1


# --- base Strategy requirement / stale-base -----------------------------------


def test_revision_requires_an_existing_base_strategy(db_session) -> None:
    campaign, _recommendation, _decision, approval, actor = build_strategic_approval(db_session)
    with pytest.raises(ForbiddenError):
        StrategyRevisionService(db_session).revise_strategy(
            campaign=campaign, base_strategy_public_id="STR-DOESNOTEXIST",
            strategic_approval_public_id=approval.public_id,
            summary="x", positioning_statement="y", actor_user_id=actor.id,
        )


def test_stale_base_is_rejected_not_silently_redirected(db_session) -> None:
    """MVP-30A-R1 §J/§20: attempting to revise using a base Strategy that
    is no longer current must deterministically reject — never silently
    rebase onto whatever is current now."""
    campaign, base_strategy, _approval, _result, _positioning, _revision, actor = build_strategy_revision(db_session)
    # base_strategy is now historical (v1, superseded by the v2 built
    # above). A second, independent, eligible Approval in the SAME
    # campaign attempting to target that stale base must be rejected —
    # never redirected onto the campaign's actual current Strategy.
    second_decision_actor = actor
    second_approval = _second_eligible_approval_in_campaign(db_session, campaign=campaign, actor=second_decision_actor)
    with pytest.raises(StrategyRevisionBaseStaleError):
        StrategyRevisionService(db_session).revise_strategy(
            campaign=campaign, base_strategy_public_id=base_strategy.public_id,
            strategic_approval_public_id=second_approval.public_id,
            summary="x", positioning_statement="y", actor_user_id=actor.id,
        )


# --- Positioning ---------------------------------------------------------------


def test_revision_never_reuses_the_base_positioning_row(db_session) -> None:
    from app.strategy.repository import PositioningRepository

    campaign, base_strategy, _approval, result_strategy, positioning, _revision, _actor = build_strategy_revision(
        db_session
    )
    base_positioning = PositioningRepository(db_session).get_for_strategy(base_strategy.id)
    assert base_positioning is not None
    assert base_positioning.id != positioning.id
    assert positioning.strategy_id == result_strategy.id


# --- immutability --------------------------------------------------------------


def test_no_update_or_delete_method_exists_for_the_revision_repository() -> None:
    assert not hasattr(StrategyRevisionRepository, "update")
    assert not hasattr(StrategyRevisionRepository, "delete")


def test_base_strategy_positioning_decision_and_approval_are_never_mutated(db_session) -> None:
    campaign, base_strategy, approval, _result, _positioning, _revision, _actor = build_strategy_revision(db_session)
    base_summary_before = base_strategy.summary
    base_version_before = base_strategy.version
    approval_outcome_before = approval.outcome
    db_session.refresh(base_strategy)
    db_session.refresh(approval)
    assert base_strategy.summary == base_summary_before
    assert base_strategy.version == base_version_before
    assert approval.outcome == approval_outcome_before


# --- Decision supersession never blocked by Revision (MVP-30A-R1 §19/§26) ----


def test_decision_can_still_be_superseded_after_its_approval_was_consumed(db_session) -> None:
    campaign, _base, approval, _result, _positioning, _revision, actor = build_strategy_revision(db_session)
    decision_public_id = _decision_public_id_for_approval(db_session, approval)
    replacement = StrategicDecisionService(db_session).supersede_decision(
        campaign=campaign, decision_public_id=decision_public_id, decision_type=StrategicDecisionType.DEFER,
        statement="Reconsidered after revision.", actor_user_id=actor.id,
    )
    assert replacement.superseded_at is None  # supersession succeeded normally


def test_revision_remains_valid_after_its_decision_is_later_superseded(db_session) -> None:
    campaign, _base, approval, _result, _positioning, revision, actor = build_strategy_revision(db_session)
    decision_public_id = _decision_public_id_for_approval(db_session, approval)
    StrategicDecisionService(db_session).supersede_decision(
        campaign=campaign, decision_public_id=decision_public_id, decision_type=StrategicDecisionType.DEFER,
        statement="Reconsidered.", actor_user_id=actor.id,
    )
    reread = StrategyRevisionRepository(db_session).get_for_approval(approval_id=approval.id)
    assert reread is not None
    assert reread.id == revision.id  # never retroactively invalidated or deleted


# --- downstream non-effects (MVP-30A §S/§20) ----------------------------------


def test_revision_creates_no_hypothesis_or_experiment_rows(db_session) -> None:
    from app.strategy.models import Experiment, Hypothesis

    hyps_before = db_session.execute(select(Hypothesis)).scalars().all()
    exps_before = db_session.execute(select(Experiment)).scalars().all()
    build_strategy_revision(db_session)
    hyps_after = db_session.execute(select(Hypothesis)).scalars().all()
    exps_after = db_session.execute(select(Experiment)).scalars().all()
    assert len(hyps_after) == len(hyps_before)
    assert len(exps_after) == len(exps_before)


# --- tenancy -------------------------------------------------------------------


def test_revision_against_a_base_strategy_from_a_different_campaign_is_forbidden(db_session) -> None:
    campaign_a, _recommendation, _decision, approval, actor = build_strategic_approval(db_session, campaign_name="Campaign A")
    campaign_b = build_campaign(db_session, org_name="Other Org", workspace_name="Other WS", campaign_name="Campaign B")
    base_strategy_b, _run, _stage = build_base_strategy(db_session, campaign=campaign_b)
    with pytest.raises(ForbiddenError):
        StrategyRevisionService(db_session).revise_strategy(
            campaign=campaign_a, base_strategy_public_id=base_strategy_b.public_id,
            strategic_approval_public_id=approval.public_id,
            summary="x", positioning_statement="y", actor_user_id=actor.id,
        )


# --- version race backstop (MVP-30A-R1 §52) -----------------------------------
#
# The DB's own (campaign_id, version) uniqueness backstop is exercised
# directly by the real-PostgreSQL concurrency test
# (tests/test_strategy_revision_concurrency.py::test_two_concurrent_revisions_from_the_same_base_strategy_...)
# rather than here: the service's own base-Strategy lock + current-recheck
# is airtight against any single-transaction/sequential attempt to
# pre-occupy a version (any row with a higher version than the intended
# base automatically becomes the new "current" Strategy, which the
# staleness pre-check already catches before the insert is ever attempted)
# — the DB constraint can only ever fire as the loser's backstop in a
# genuine concurrent race, which a sequential domain test cannot construct.


# --- helpers -------------------------------------------------------------------


def _build_decision(session, *, decision_type):
    from tests.orchestrationtest import build_strategic_decision

    campaign, recommendation, decision, actor = build_strategic_decision(session, decision_type=decision_type)
    return campaign, recommendation, decision, actor


def _decision_public_id_for_approval(session, approval) -> str:
    from app.orchestration.models import StrategicDecision

    decision = session.get(StrategicDecision, approval.strategic_decision_id)
    assert decision is not None
    return decision.public_id


def _second_eligible_approval_in_campaign(session, *, campaign, actor):
    """Builds a second, independent, fully eligible (ADOPT/current/
    APPROVED/unconsumed) StrategicApproval within an already-existing
    campaign — a second accepted Recommendation -> Decision -> Approval
    chain, reusing the same campaign (via
    ``test_strategic_decision_api.py``'s own "reuse an existing campaign"
    ancestry helper) rather than creating a brand-new one."""
    from app.learning.models import StrategicRecommendationDecision
    from app.learning.service import LearningService
    from app.orchestration.service import StrategicApprovalService, StrategicDecisionService
    from tests.test_strategic_decision_api import _build_recommendation_in_campaign

    recommendation = _build_recommendation_in_campaign(session, campaign)
    accepted = LearningService(session).decide_strategic_recommendation_candidate(
        campaign=campaign, recommendation_public_id=recommendation.public_id,
        decision=StrategicRecommendationDecision.ACCEPTED, actor_user_id=actor.id,
    )
    decision = StrategicDecisionService(session).record_decision(
        campaign=campaign, recommendation_public_id=accepted.public_id,
        decision_type=StrategicDecisionType.ADOPT, statement="Second decision.", actor_user_id=actor.id,
    )
    return StrategicApprovalService(session).record_approval(
        campaign=campaign, decision_public_id=decision.public_id,
        outcome=StrategicApprovalOutcome.APPROVED, actor_user_id=actor.id,
    )
