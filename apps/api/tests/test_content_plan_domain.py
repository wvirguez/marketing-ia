"""Domain/service-level tests for Governed Content Plan creation (MVP-33B,
implementing the frozen MVP-33A/-33A-R1 contract). All marked `postgres`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.api_errors import ForbiddenError
from app.planning.models import ContentPlanOrigin
from app.planning.service import PlanningService
from tests.planningtest import planning_campaign  # noqa: F401
from tests.strategytest import build_current_experiment, build_current_hypothesis
from tests.contenttest import make_user

pytestmark = pytest.mark.postgres


# --- Case G: generic governed plan ----------------------------------------


def test_generic_plan_can_be_created_with_no_experiment(db_session, planning_campaign) -> None:
    campaign, _run, _stages = planning_campaign
    actor = make_user(db_session)
    plan, items = PlanningService(db_session).create_plan(
        campaign=campaign, summary="A generic, non-experimental content plan.",
        experiment_public_id=None, actor_user_id=actor.id,
    )
    assert plan.public_id.startswith("PLN-")
    assert plan.origin is ContentPlanOrigin.GOVERNED
    assert plan.campaign_run_id is None
    assert plan.stage_execution_id is None
    assert plan.experiment_id is None
    assert items == []


def test_zero_item_governed_plan_is_valid(db_session, planning_campaign) -> None:
    campaign, _run, _stages = planning_campaign
    actor = make_user(db_session)
    plan, items = PlanningService(db_session).create_plan(
        campaign=campaign, summary="x", experiment_public_id=None, items=[], actor_user_id=actor.id,
    )
    assert plan is not None
    assert items == []


def test_governed_plan_can_include_optional_initial_items(db_session, planning_campaign) -> None:
    campaign, _run, _stages = planning_campaign
    actor = make_user(db_session)
    plan, items = PlanningService(db_session).create_plan(
        campaign=campaign, summary="x",
        experiment_public_id=None,
        items=[
            {"format": "Reel", "objective": "Introduce the offer.", "sequence": 1, "scheduled_date": None},
            {"format": "Email", "objective": "Follow up.", "sequence": 2, "scheduled_date": None},
        ],
        actor_user_id=actor.id,
    )
    assert len(items) == 2
    assert [i.sequence for i in items] == [1, 2]


# --- Case E: Experiment-derived governed plan ------------------------------


def test_experiment_derived_plan_can_be_created(db_session) -> None:
    campaign, _strategy, _hypothesis, experiment, actor = build_current_experiment(db_session)
    plan, _items = PlanningService(db_session).create_plan(
        campaign=campaign, summary="Operationalizes the onboarding A/B test.",
        experiment_public_id=experiment.public_id, actor_user_id=actor.id,
    )
    assert plan.experiment_id == experiment.id
    assert plan.origin is ContentPlanOrigin.GOVERNED


def test_historical_strategy_experiment_remains_eligible_for_plan_creation(db_session) -> None:
    """MVP-33A §N (E2 ALLOWED): a Plan may reference an Experiment whose
    Hypothesis's Strategy has since become historical — no currency
    recheck, no rejection."""
    from tests.test_experiment_domain import _make_strategy_historical

    campaign, strategy, _hypothesis, experiment, actor = build_current_experiment(db_session)
    _make_strategy_historical(db_session, campaign=campaign, strategy=strategy, actor=actor)

    plan, _items = PlanningService(db_session).create_plan(
        campaign=campaign, summary="Plans content for a now-historical experiment.",
        experiment_public_id=experiment.public_id, actor_user_id=actor.id,
    )
    assert plan.experiment_id == experiment.id


def test_unknown_experiment_public_id_is_forbidden(db_session, planning_campaign) -> None:
    campaign, _run, _stages = planning_campaign
    actor = make_user(db_session)
    with pytest.raises(ForbiddenError):
        PlanningService(db_session).create_plan(
            campaign=campaign, summary="x", experiment_public_id="EXP-TOTALLYFAKE0", actor_user_id=actor.id,
        )


def test_experiment_from_a_different_campaign_same_workspace_is_forbidden(db_session) -> None:
    """MVP-33A-R1 §D/§I: same-Workspace alone is not sufficient — the
    Experiment must belong to THIS Campaign's own ancestry. Constructs a
    genuine same-workspace scenario (not accidentally cross-workspace,
    which ``build_current_experiment`` alone would produce, since it
    always creates a brand-new Organization+Workspace per call)."""
    campaign_a, _strategy_a, _hyp_a, actor_a = build_current_hypothesis(db_session, campaign_name="Campaign A")
    _campaign_b, _strategy_b, _hyp_b, experiment_b, _actor_b = build_current_experiment(
        db_session, campaign_name="Campaign B", within_workspace_id=campaign_a.workspace_id,
    )
    assert experiment_b.workspace_id == campaign_a.workspace_id  # genuinely same workspace

    with pytest.raises(ForbiddenError):
        PlanningService(db_session).create_plan(
            campaign=campaign_a, summary="x", experiment_public_id=experiment_b.public_id,
            actor_user_id=actor_a.id,
        )


# --- duplicates / cardinality ------------------------------------------------


def test_multiple_plan_versions_may_reference_the_same_experiment(db_session) -> None:
    campaign, _strategy, _hypothesis, experiment, actor = build_current_experiment(db_session)
    service = PlanningService(db_session)
    first, _ = service.create_plan(
        campaign=campaign, summary="First plan.", experiment_public_id=experiment.public_id, actor_user_id=actor.id,
    )
    second, _ = service.create_plan(
        campaign=campaign, summary="Second plan.", experiment_public_id=experiment.public_id, actor_user_id=actor.id,
    )
    assert first.id != second.id
    assert first.experiment_id == second.experiment_id == experiment.id
    assert second.version == first.version + 1


# --- versioning ---------------------------------------------------------------


def test_governed_version_continues_from_current_max(db_session, planning_campaign) -> None:
    campaign, _run, _stages = planning_campaign
    actor = make_user(db_session)
    service = PlanningService(db_session)
    first, _ = service.create_plan(campaign=campaign, summary="v1", experiment_public_id=None, actor_user_id=actor.id)
    second, _ = service.create_plan(campaign=campaign, summary="v2", experiment_public_id=None, actor_user_id=actor.id)
    assert first.version == 1
    assert second.version == 2


# --- immutability / no update surface ----------------------------------------


def test_no_update_or_delete_method_exists_for_the_content_plan_repository() -> None:
    from app.planning.repository import ContentPlanRepository

    assert not hasattr(ContentPlanRepository, "update")
    assert not hasattr(ContentPlanRepository, "delete")


# --- downstream non-effects ---------------------------------------------------


def test_creation_creates_no_content_brief_rows(db_session) -> None:
    from app.content.models import ContentBrief

    briefs_before = db_session.execute(select(ContentBrief)).scalars().all()
    campaign, _strategy, _hypothesis, experiment, actor = build_current_experiment(db_session)
    PlanningService(db_session).create_plan(
        campaign=campaign, summary="x", experiment_public_id=experiment.public_id, actor_user_id=actor.id,
    )
    briefs_after = db_session.execute(select(ContentBrief)).scalars().all()
    assert len(briefs_after) == len(briefs_before)


def test_creation_never_mutates_the_experiment_or_its_ancestry(db_session) -> None:
    campaign, strategy, hypothesis, experiment, actor = build_current_experiment(db_session)
    description_before = experiment.description
    status_before = experiment.status
    hypothesis_statement_before = hypothesis.statement
    strategy_summary_before = strategy.summary

    PlanningService(db_session).create_plan(
        campaign=campaign, summary="x", experiment_public_id=experiment.public_id, actor_user_id=actor.id,
    )
    db_session.refresh(experiment)
    db_session.refresh(hypothesis)
    db_session.refresh(strategy)
    assert experiment.description == description_before
    assert experiment.status == status_before
    assert hypothesis.statement == hypothesis_statement_before
    assert strategy.summary == strategy_summary_before


# --- tenancy -------------------------------------------------------------------


def test_creation_against_an_unknown_campaign_context_never_leaks_cross_workspace_experiment(db_session) -> None:
    """Cross-workspace: an Experiment belonging to a wholly separate
    workspace/campaign must resolve to the exact same non-leaky rejection
    as an unknown public_id."""
    from tests.researchtest import build_campaign_run_with_stages

    campaign_a, _run, _stages = build_campaign_run_with_stages(db_session, campaign_name="Tenancy Campaign A")
    actor_a = make_user(db_session)
    _campaign_b, _strategy_b, _hyp_b, experiment_b, _actor_b = build_current_experiment(
        db_session, campaign_name="Tenancy Campaign B"
    )

    with pytest.raises(ForbiddenError):
        PlanningService(db_session).create_plan(
            campaign=campaign_a, summary="x", experiment_public_id=experiment_b.public_id, actor_user_id=actor_a.id,
        )
