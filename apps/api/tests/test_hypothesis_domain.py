"""Domain/service-level tests for Governed Next-Cycle Hypothesis creation
(MVP-31B, implementing the frozen MVP-31A/-31A-R1 contract). All marked
`postgres`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.api_errors import ForbiddenError, HypothesisStrategyStaleError
from app.strategy.models import Experiment, Hypothesis, HypothesisStatus
from app.strategy.repository import HypothesisRepository
from app.strategy.service import StrategyService
from tests.commercialtest import build_campaign
from tests.orchestrationtest import build_base_strategy, build_strategic_approval, build_strategy_revision

pytestmark = pytest.mark.postgres


# --- happy path: any current Strategy origin is eligible ---------------------


def test_hypothesis_can_be_created_under_a_current_bootstrap_strategy(db_session) -> None:
    campaign, _recommendation, _decision, _approval, actor = build_strategic_approval(db_session)
    base_strategy, _run, _stage = build_base_strategy(db_session, campaign=campaign)

    hypothesis = StrategyService(db_session).create_hypothesis(
        campaign=campaign, strategy_public_id=base_strategy.public_id,
        statement="First-time buyers respond better to a guarantee.", actor_user_id=actor.id,
    )
    assert hypothesis.public_id.startswith("HYP-")
    assert hypothesis.strategy_id == base_strategy.id
    assert hypothesis.workspace_id == base_strategy.workspace_id
    assert hypothesis.status is HypothesisStatus.OPEN
    assert hypothesis.statement == "First-time buyers respond better to a guarantee."


def test_hypothesis_can_be_created_under_a_current_revision_strategy(db_session) -> None:
    """MVP-31A §H: eligibility is not restricted to REVISION-origin —
    this confirms the converse also holds, since ``build_strategy_revision``
    leaves a REVISION-origin Strategy as the campaign's current one."""
    campaign, _base, _approval, result_strategy, _positioning, _revision, actor = build_strategy_revision(db_session)

    hypothesis = StrategyService(db_session).create_hypothesis(
        campaign=campaign, strategy_public_id=result_strategy.public_id,
        statement="A revised hook increases signups.", actor_user_id=actor.id,
    )
    assert hypothesis.strategy_id == result_strategy.id


# --- current-Strategy eligibility / stale rejection ---------------------------


def test_hypothesis_requires_an_existing_strategy(db_session) -> None:
    campaign, _recommendation, _decision, _approval, actor = build_strategic_approval(db_session)
    with pytest.raises(ForbiddenError):
        StrategyService(db_session).create_hypothesis(
            campaign=campaign, strategy_public_id="STR-DOESNOTEXIST", statement="x", actor_user_id=actor.id,
        )


def test_stale_strategy_is_rejected_not_silently_redirected(db_session) -> None:
    """MVP-31A §8/§G: a Hypothesis request naming a Strategy that is no
    longer current must deterministically reject — never silently
    reattach to whatever is current now."""
    campaign, base_strategy, _approval, _result, _positioning, _revision, actor = build_strategy_revision(db_session)
    # base_strategy is now historical (v1, superseded by the v2 the
    # revision produced above).
    with pytest.raises(HypothesisStrategyStaleError):
        StrategyService(db_session).create_hypothesis(
            campaign=campaign, strategy_public_id=base_strategy.public_id,
            statement="Attempted against a historical version.", actor_user_id=actor.id,
        )


# --- duplication / idempotency (MVP-31A §N) -----------------------------------


def test_duplicate_statements_are_allowed(db_session) -> None:
    campaign, _recommendation, _decision, _approval, actor = build_strategic_approval(db_session)
    base_strategy, _run, _stage = build_base_strategy(db_session, campaign=campaign)
    service = StrategyService(db_session)

    first = service.create_hypothesis(
        campaign=campaign, strategy_public_id=base_strategy.public_id, statement="Same statement.", actor_user_id=actor.id,
    )
    second = service.create_hypothesis(
        campaign=campaign, strategy_public_id=base_strategy.public_id, statement="Same statement.", actor_user_id=actor.id,
    )
    assert first.id != second.id
    assert first.statement == second.statement


# --- no origin discriminator (MVP-31A-R1) -------------------------------------


def test_hypothesis_has_no_origin_column() -> None:
    """MVP-31A-R1: the proposed BOOTSTRAP/NEXT_CYCLE discriminator was
    rejected as not a durable domain distinction — confirms it was never
    added."""
    assert "origin" not in Hypothesis.__table__.columns


# --- immutability --------------------------------------------------------------


def test_no_update_or_delete_method_exists_for_the_hypothesis_repository() -> None:
    assert not hasattr(HypothesisRepository, "update")
    assert not hasattr(HypothesisRepository, "delete")


def test_strategy_and_workspace_are_never_mutated_by_creation(db_session) -> None:
    campaign, _recommendation, _decision, _approval, actor = build_strategic_approval(db_session)
    base_strategy, _run, _stage = build_base_strategy(db_session, campaign=campaign)
    summary_before = base_strategy.summary
    version_before = base_strategy.version

    StrategyService(db_session).create_hypothesis(
        campaign=campaign, strategy_public_id=base_strategy.public_id, statement="x", actor_user_id=actor.id,
    )
    db_session.refresh(base_strategy)
    assert base_strategy.summary == summary_before
    assert base_strategy.version == version_before


# --- downstream non-effects (MVP-31A §35/§AE) ---------------------------------


def test_creation_creates_no_experiment_rows(db_session) -> None:
    campaign, _recommendation, _decision, _approval, actor = build_strategic_approval(db_session)
    base_strategy, _run, _stage = build_base_strategy(db_session, campaign=campaign)

    exps_before = db_session.execute(select(Experiment)).scalars().all()
    StrategyService(db_session).create_hypothesis(
        campaign=campaign, strategy_public_id=base_strategy.public_id, statement="x", actor_user_id=actor.id,
    )
    exps_after = db_session.execute(select(Experiment)).scalars().all()
    assert len(exps_after) == len(exps_before)


# --- tenancy -------------------------------------------------------------------


def test_creation_against_a_strategy_from_a_different_campaign_is_forbidden(db_session) -> None:
    campaign_a, _recommendation, _decision, _approval, actor = build_strategic_approval(db_session, campaign_name="Campaign A")
    campaign_b = build_campaign(db_session, org_name="Other Org", workspace_name="Other WS", campaign_name="Campaign B")
    base_strategy_b, _run, _stage = build_base_strategy(db_session, campaign=campaign_b)
    with pytest.raises(ForbiddenError):
        StrategyService(db_session).create_hypothesis(
            campaign=campaign_a, strategy_public_id=base_strategy_b.public_id, statement="x", actor_user_id=actor.id,
        )
