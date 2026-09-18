"""Domain/service-level tests for Governed Experiment creation (MVP-32B,
implementing the frozen MVP-32A/-32A-R1 contract). All marked `postgres`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.api_errors import ExperimentStrategyStaleError, ForbiddenError
from app.orchestration.models import StrategicApprovalOutcome, StrategicDecisionType
from app.orchestration.service import StrategicApprovalService, StrategicDecisionService, StrategyRevisionService
from app.strategy.repository import ExperimentRepository
from app.strategy.service import StrategyService
from tests.strategytest import build_current_hypothesis

pytestmark = pytest.mark.postgres


# --- happy path ----------------------------------------------------------------


def test_experiment_can_be_created_under_a_current_strategy_hypothesis(db_session) -> None:
    campaign, strategy, hypothesis, actor = build_current_hypothesis(db_session)

    experiment = StrategyService(db_session).create_experiment(
        campaign=campaign, hypothesis_public_id=hypothesis.public_id,
        description="A/B test two onboarding email sequences against a held-out control group.",
        actor_user_id=actor.id,
    )
    assert experiment.public_id.startswith("EXP-")
    assert experiment.hypothesis_id == hypothesis.id
    assert experiment.workspace_id == hypothesis.workspace_id
    assert experiment.description == "A/B test two onboarding email sequences against a held-out control group."


def test_status_is_server_controlled_recorded(db_session) -> None:
    campaign, _strategy, hypothesis, actor = build_current_hypothesis(db_session)
    experiment = StrategyService(db_session).create_experiment(
        campaign=campaign, hypothesis_public_id=hypothesis.public_id, description="x", actor_user_id=actor.id,
    )
    assert experiment.status == "RECORDED"


def test_no_experiment_status_enum_exists() -> None:
    """MVP-32A-R1 §10-13: a single-value vocabulary does not justify a
    migration — experiments.status remains the existing nullable
    String(30), never a native enum."""
    from app.strategy.models import Experiment

    column = Experiment.__table__.columns["status"]
    assert column.nullable is True
    assert not hasattr(column.type, "enums")  # a native Enum type exposes .enums; String does not


# --- eligibility: any Hypothesis status ----------------------------------------


def test_experiment_creation_does_not_require_confirmed_hypothesis(db_session) -> None:
    """MVP-32A §L: OPEN is eligible — the experiment exists precisely to
    test its still-open parent Hypothesis. No status restriction exists."""
    from app.strategy.models import HypothesisStatus

    campaign, _strategy, hypothesis, actor = build_current_hypothesis(db_session)
    assert hypothesis.status is HypothesisStatus.OPEN
    experiment = StrategyService(db_session).create_experiment(
        campaign=campaign, hypothesis_public_id=hypothesis.public_id, description="x", actor_user_id=actor.id,
    )
    assert experiment.hypothesis_id == hypothesis.id


# --- current-Strategy eligibility / historical rejection -----------------------


def test_experiment_requires_an_existing_hypothesis(db_session) -> None:
    campaign, _strategy, _hypothesis, actor = build_current_hypothesis(db_session)
    with pytest.raises(ForbiddenError):
        StrategyService(db_session).create_experiment(
            campaign=campaign, hypothesis_public_id="HYP-DOESNOTEXIST", description="x", actor_user_id=actor.id,
        )


def test_historical_strategy_hypothesis_is_rejected(db_session) -> None:
    """MVP-32A-R1 §14 (Option A): a Hypothesis whose parent Strategy has
    since become historical is NOT eligible for a new Experiment —
    deterministic 409, never silently created against stale ancestry."""
    campaign, strategy, hypothesis, actor = build_current_hypothesis(db_session)
    _make_strategy_historical(db_session, campaign=campaign, strategy=strategy, actor=actor)

    with pytest.raises(ExperimentStrategyStaleError):
        StrategyService(db_session).create_experiment(
            campaign=campaign, hypothesis_public_id=hypothesis.public_id,
            description="attempted against historical Strategy", actor_user_id=actor.id,
        )


# --- duplication / cardinality --------------------------------------------------


def test_duplicate_descriptions_are_allowed(db_session) -> None:
    campaign, _strategy, hypothesis, actor = build_current_hypothesis(db_session)
    service = StrategyService(db_session)
    first = service.create_experiment(
        campaign=campaign, hypothesis_public_id=hypothesis.public_id, description="Same description.", actor_user_id=actor.id,
    )
    second = service.create_experiment(
        campaign=campaign, hypothesis_public_id=hypothesis.public_id, description="Same description.", actor_user_id=actor.id,
    )
    assert first.id != second.id
    assert first.description == second.description


def test_multiple_experiments_under_the_same_hypothesis_allowed(db_session) -> None:
    campaign, _strategy, hypothesis, actor = build_current_hypothesis(db_session)
    service = StrategyService(db_session)
    service.create_experiment(campaign=campaign, hypothesis_public_id=hypothesis.public_id, description="First test.", actor_user_id=actor.id)
    service.create_experiment(campaign=campaign, hypothesis_public_id=hypothesis.public_id, description="Second test.", actor_user_id=actor.id)
    rows = ExperimentRepository(db_session).list_for_hypothesis(hypothesis.id)
    assert len(rows) == 2


# --- immutability ----------------------------------------------------------------


def test_no_update_or_delete_method_exists_for_the_experiment_repository() -> None:
    assert not hasattr(ExperimentRepository, "update")
    assert not hasattr(ExperimentRepository, "delete")


def test_hypothesis_and_strategy_are_never_mutated_by_creation(db_session) -> None:
    campaign, strategy, hypothesis, actor = build_current_hypothesis(db_session)
    statement_before = hypothesis.statement
    status_before = hypothesis.status
    summary_before = strategy.summary
    version_before = strategy.version

    StrategyService(db_session).create_experiment(
        campaign=campaign, hypothesis_public_id=hypothesis.public_id, description="x", actor_user_id=actor.id,
    )
    db_session.refresh(hypothesis)
    db_session.refresh(strategy)
    assert hypothesis.statement == statement_before
    assert hypothesis.status == status_before
    assert strategy.summary == summary_before
    assert strategy.version == version_before


# --- downstream non-effects (MVP-32A §28/§AA) -----------------------------------


def test_creation_creates_no_content_plan_rows(db_session) -> None:
    from app.planning.models import ContentPlan

    plans_before = db_session.execute(select(ContentPlan)).scalars().all()
    campaign, _strategy, hypothesis, actor = build_current_hypothesis(db_session)
    StrategyService(db_session).create_experiment(
        campaign=campaign, hypothesis_public_id=hypothesis.public_id, description="x", actor_user_id=actor.id,
    )
    plans_after = db_session.execute(select(ContentPlan)).scalars().all()
    assert len(plans_after) == len(plans_before)


# --- tenancy -----------------------------------------------------------------------


def test_creation_against_a_hypothesis_from_a_different_campaign_is_forbidden(db_session) -> None:
    campaign_a, _strategy_a, _hyp_a, actor_a = build_current_hypothesis(db_session, campaign_name="Campaign A")
    _campaign_b, _strategy_b, hypothesis_b, _actor_b = build_current_hypothesis(db_session, campaign_name="Campaign B")

    with pytest.raises(ForbiddenError):
        StrategyService(db_session).create_experiment(
            campaign=campaign_a, hypothesis_public_id=hypothesis_b.public_id, description="x", actor_user_id=actor_a.id,
        )


# --- helpers -----------------------------------------------------------------------


def _make_strategy_historical(session, *, campaign, strategy, actor):
    """Builds a full, independent StrategicApproval ancestry within the
    SAME already-existing ``campaign`` (mirrors
    ``tests/test_strategy_revision_concurrency.py::_second_eligible_approval``
    exactly) and uses it to record a real governed StrategyRevision against
    ``strategy``, making it historical."""
    from app.learning.models import StrategicRecommendationDecision
    from app.learning.service import LearningService
    from tests.test_strategic_decision_api import _build_recommendation_in_campaign

    recommendation = _build_recommendation_in_campaign(session, campaign)
    accepted = LearningService(session).decide_strategic_recommendation_candidate(
        campaign=campaign, recommendation_public_id=recommendation.public_id,
        decision=StrategicRecommendationDecision.ACCEPTED, actor_user_id=actor.id,
    )
    decision = StrategicDecisionService(session).record_decision(
        campaign=campaign, recommendation_public_id=accepted.public_id,
        decision_type=StrategicDecisionType.ADOPT, statement="Adopt for revision.", actor_user_id=actor.id,
    )
    approval = StrategicApprovalService(session).record_approval(
        campaign=campaign, decision_public_id=decision.public_id,
        outcome=StrategicApprovalOutcome.APPROVED, actor_user_id=actor.id,
    )
    StrategyRevisionService(session).revise_strategy(
        campaign=campaign, base_strategy_public_id=strategy.public_id,
        strategic_approval_public_id=approval.public_id,
        summary="Revised.", positioning_statement="Revised positioning.", actor_user_id=actor.id,
    )
