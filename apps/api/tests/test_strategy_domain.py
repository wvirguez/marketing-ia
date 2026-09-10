"""Strategy domain persistence, provenance, versioning, Hypothesis status,
Experiment boundary, and governance-boundary tests (BACKEND-08 §6-§14/§20).
All marked `postgres`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.campaigns.repository import CampaignRepository, CampaignRunRepository
from app.core.api_errors import (
    ForbiddenError,
    InvalidLifecycleTransitionError,
    ProvenanceMismatchError,
    VersionConflictError,
)
from app.orchestration.models import BusinessStage
from app.orchestration.repository import RunStageExecutionRepository
from app.strategy.models import Experiment, Hypothesis, HypothesisStatus, Positioning, Strategy
from app.strategy.repository import StrategyRepository
from app.strategy.service import StrategyService
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.researchtest import build_campaign_run_with_stages
from tests.strategytest import default_experiment, default_hypothesis

pytestmark = pytest.mark.postgres


# --- domain persistence ------------------------------------------------


def test_strategy_positioning_and_hypotheses_persist(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)

    strategy, positioning, hypotheses, experiments = service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Position as the structured, beginner-friendly training method.",
        positioning_statement="For first-time dog owners who feel overwhelmed by generic advice.",
        hypotheses=[default_hypothesis(), default_hypothesis(statement="A second, distinct hypothesis.")],
    )

    assert strategy.public_id.startswith("STR-")
    assert strategy.version == 1
    assert positioning.public_id.startswith("POS-")
    assert positioning.strategy_id == strategy.id
    assert len(hypotheses) == 2
    assert all(h.public_id.startswith("HYP-") for h in hypotheses)
    assert all(h.strategy_id == strategy.id for h in hypotheses)
    assert all(h.status is HypothesisStatus.OPEN for h in hypotheses)
    assert experiments == []


def test_positioning_is_exactly_one_per_strategy(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    strategy, positioning, _hyps, _exps = StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.",
    )
    rogue = Positioning(public_id="POS-DUPTEST00001", strategy_id=strategy.id, statement="A second one.")
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_hypotheses_may_carry_initial_experiments(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    strategy, _positioning, hypotheses, experiments = StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.",
        hypotheses=[default_hypothesis(experiments=[default_experiment(), default_experiment()])],
    )
    assert len(hypotheses) == 1
    assert len(experiments) == 2
    assert all(e.public_id.startswith("EXP-") for e in experiments)
    assert all(e.hypothesis_id == hypotheses[0].id for e in experiments)
    assert all(e.status is None for e in experiments)


def test_strategy_may_be_created_with_no_hypotheses(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    strategy, positioning, hypotheses, experiments = StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.",
    )
    assert strategy is not None
    assert positioning is not None
    assert hypotheses == []
    assert experiments == []


# --- provenance: valid -------------------------------------------------


def test_strategy_provenance_matches_campaign_run_and_stage(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    strategy, *_ = StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.",
    )
    assert strategy.campaign_id == campaign.id
    assert strategy.campaign_run_id == run.id
    assert strategy.stage_execution_id == stages[BusinessStage.STRATEGY].id


# --- provenance: invalid -------------------------------------------------


def test_cross_campaign_campaign_run_is_rejected(db_session) -> None:
    campaign_a, run_a, stages_a = build_campaign_run_with_stages(db_session, campaign_name="Campaign A")
    campaign_b, _run_b, _stages_b = build_campaign_run_with_stages(db_session, campaign_name="Campaign B")

    with pytest.raises(ProvenanceMismatchError):
        StrategyService(db_session).record_strategy(
            campaign=campaign_b, campaign_run=run_a, stage_execution=stages_a[BusinessStage.STRATEGY],
            summary="Should be rejected.", positioning_statement="Statement.",
        )


def test_cross_workspace_campaign_run_is_rejected(db_session) -> None:
    campaign_a, run_a, stages_a = build_campaign_run_with_stages(
        db_session, org_name="Org A", workspace_name="WS A", campaign_name="Campaign A"
    )
    organization_b = OrganizationRepository(db_session).create(name="Org B")
    workspace_b = WorkspaceRepository(db_session).create(organization_id=organization_b.id, name="WS B")
    campaign_b = CampaignRepository(db_session).create(workspace_id=workspace_b.id, name="Campaign B (WS B)")
    db_session.flush()

    with pytest.raises(ProvenanceMismatchError):
        StrategyService(db_session).record_strategy(
            campaign=campaign_b, campaign_run=run_a, stage_execution=stages_a[BusinessStage.STRATEGY],
            summary="Should be rejected.", positioning_statement="Statement.",
        )


def test_stage_execution_from_a_different_run_is_rejected(db_session) -> None:
    campaign_a, run_a, _stages_a = build_campaign_run_with_stages(db_session, campaign_name="Campaign A")
    campaign_b, run_b, stages_b = build_campaign_run_with_stages(db_session, campaign_name="Campaign B")

    with pytest.raises(ProvenanceMismatchError):
        StrategyService(db_session).record_strategy(
            campaign=campaign_a, campaign_run=run_a, stage_execution=stages_b[BusinessStage.STRATEGY],
            summary="Should be rejected.", positioning_statement="Statement.",
        )


def test_non_strategy_stage_is_rejected(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    with pytest.raises(ProvenanceMismatchError):
        StrategyService(db_session).record_strategy(
            campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH],
            summary="Should be rejected.", positioning_statement="Statement.",
        )


def test_database_rejects_a_strategy_with_mismatched_workspace(db_session) -> None:
    """Bypasses the service entirely — direct model construction proves
    the composite FK itself rejects a workspace mismatch."""
    campaign, run, stages = build_campaign_run_with_stages(db_session)
    other_workspace = WorkspaceRepository(db_session).create(
        organization_id=OrganizationRepository(db_session).create(name="Rogue Org").id, name="Rogue WS"
    )
    db_session.flush()

    rogue_strategy = Strategy(
        public_id="STR-MISMATCHTEST",
        workspace_id=other_workspace.id,
        campaign_id=campaign.id,
        campaign_run_id=run.id,
        stage_execution_id=stages[BusinessStage.STRATEGY].id,
        version=1,
        summary="Should be rejected at the DB level.",
    )
    db_session.add(rogue_strategy)
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- tenancy: composite FK on Hypothesis/Experiment -----------------------


def test_database_rejects_a_hypothesis_with_mismatched_workspace(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    strategy, *_ = StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.",
    )
    other_workspace = WorkspaceRepository(db_session).create(
        organization_id=OrganizationRepository(db_session).create(name="Rogue Org 2").id, name="Rogue WS 2"
    )
    db_session.flush()

    rogue = Hypothesis(
        public_id="HYP-MISMATCHTEST",
        workspace_id=other_workspace.id,
        strategy_id=strategy.id,
        statement="Should be rejected.",
        status=HypothesisStatus.OPEN,
    )
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_database_rejects_an_experiment_with_mismatched_workspace(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    _strategy, _positioning, hypotheses, _exps = StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.", hypotheses=[default_hypothesis()],
    )
    other_workspace = WorkspaceRepository(db_session).create(
        organization_id=OrganizationRepository(db_session).create(name="Rogue Org 3").id, name="Rogue WS 3"
    )
    db_session.flush()

    rogue = Experiment(
        public_id="EXP-MISMATCHTEST",
        workspace_id=other_workspace.id,
        hypothesis_id=hypotheses[0].id,
        description="Should be rejected.",
    )
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- versioning ----------------------------------------------------------


def test_versions_are_unique_per_campaign(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    first, *_ = service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="v1", positioning_statement="Statement.",
    )
    assert first.version == 1

    second, *_ = service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="v2", positioning_statement="Statement.",
    )
    assert second.version == 2
    assert first.id != second.id


def test_duplicate_version_is_rejected_at_the_database_level(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    db_session.add(
        Strategy(
            public_id="STR-DUPTEST0001", workspace_id=campaign.workspace_id, campaign_id=campaign.id,
            campaign_run_id=run.id, stage_execution_id=stages[BusinessStage.STRATEGY].id,
            version=1, summary="first",
        )
    )
    db_session.flush()
    db_session.add(
        Strategy(
            public_id="STR-DUPTEST0002", workspace_id=campaign.workspace_id, campaign_id=campaign.id,
            campaign_run_id=run.id, stage_execution_id=stages[BusinessStage.STRATEGY].id,
            version=1, summary="duplicate",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_service_maps_version_conflict_to_a_deterministic_error(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="v1", positioning_statement="Statement.",
    )
    with patch.object(StrategyRepository, "next_version_for_campaign", return_value=1):
        with pytest.raises(VersionConflictError):
            service.record_strategy(
                campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
                summary="racing write", positioning_statement="Statement.",
            )


def test_current_strategy_is_the_highest_version(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    service.record_strategy(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY], summary="v1", positioning_statement="s1")
    service.record_strategy(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY], summary="v2", positioning_statement="s2")
    latest, positioning, _hyps, _exps = service.get_strategy_output(campaign=campaign)
    assert latest.version == 2
    assert latest.summary == "v2"
    assert positioning.statement == "s2"


def test_old_version_remains_persisted_after_a_new_one_is_created(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    first, *_ = service.record_strategy(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY], summary="v1", positioning_statement="s1")
    service.record_strategy(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY], summary="v2", positioning_statement="s2")

    reloaded = db_session.execute(select(Strategy).where(Strategy.id == first.id)).scalar_one()
    assert reloaded.summary == "v1"
    assert reloaded.version == 1


def test_two_campaign_runs_do_not_overwrite_each_others_strategies(db_session) -> None:
    organization = OrganizationRepository(db_session).create(name="History Org 2")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="History WS 2")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="History Campaign 2")
    run_1 = CampaignRunRepository(db_session).create(campaign=campaign, run_number=1)
    db_session.flush()
    stages_1 = RunStageExecutionRepository(db_session).materialize_for_run(campaign_run=run_1)
    run_2 = CampaignRunRepository(db_session).create(campaign=campaign, run_number=2)
    db_session.flush()
    stages_2 = RunStageExecutionRepository(db_session).materialize_for_run(campaign_run=run_2)

    service = StrategyService(db_session)
    strategy_1, *_ = service.record_strategy(
        campaign=campaign, campaign_run=run_1,
        stage_execution=next(s for s in stages_1 if s.stage is BusinessStage.STRATEGY),
        summary="From run 1.", positioning_statement="s1",
    )
    strategy_2, *_ = service.record_strategy(
        campaign=campaign, campaign_run=run_2,
        stage_execution=next(s for s in stages_2 if s.stage is BusinessStage.STRATEGY),
        summary="From run 2.", positioning_statement="s2",
    )
    assert strategy_1.version == 1
    assert strategy_2.version == 2
    assert strategy_1.campaign_run_id == run_1.id
    assert strategy_2.campaign_run_id == run_2.id
    stored = db_session.execute(select(Strategy).where(Strategy.campaign_id == campaign.id)).scalars().all()
    assert len(stored) == 2


# --- hypothesis status transitions ----------------------------------------


def test_hypothesis_default_status_is_open(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    _strategy, _pos, hypotheses, _exps = StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.", hypotheses=[default_hypothesis()],
    )
    assert hypotheses[0].status is HypothesisStatus.OPEN


def test_open_to_confirmed_is_authorized(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    _strategy, _pos, hypotheses, _exps = service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.", hypotheses=[default_hypothesis()],
    )
    updated = service.transition_hypothesis(
        campaign=campaign, hypothesis_public_id=hypotheses[0].public_id, target_status=HypothesisStatus.CONFIRMED,
    )
    assert updated.status is HypothesisStatus.CONFIRMED


def test_open_to_refuted_is_authorized(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    _strategy, _pos, hypotheses, _exps = service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.", hypotheses=[default_hypothesis()],
    )
    updated = service.transition_hypothesis(
        campaign=campaign, hypothesis_public_id=hypotheses[0].public_id, target_status=HypothesisStatus.REFUTED,
    )
    assert updated.status is HypothesisStatus.REFUTED


@pytest.mark.parametrize(
    "target",
    [HypothesisStatus.OPEN, HypothesisStatus.CONFIRMED, HypothesisStatus.REFUTED],
)
def test_confirmed_has_no_outgoing_transition(strategy_campaign, db_session, target) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    _strategy, _pos, hypotheses, _exps = service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.", hypotheses=[default_hypothesis()],
    )
    service.transition_hypothesis(
        campaign=campaign, hypothesis_public_id=hypotheses[0].public_id, target_status=HypothesisStatus.CONFIRMED,
    )
    with pytest.raises(InvalidLifecycleTransitionError):
        service.transition_hypothesis(
            campaign=campaign, hypothesis_public_id=hypotheses[0].public_id, target_status=target,
        )


@pytest.mark.parametrize(
    "target",
    [HypothesisStatus.OPEN, HypothesisStatus.CONFIRMED, HypothesisStatus.REFUTED],
)
def test_refuted_has_no_outgoing_transition(strategy_campaign, db_session, target) -> None:
    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    _strategy, _pos, hypotheses, _exps = service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.", hypotheses=[default_hypothesis()],
    )
    service.transition_hypothesis(
        campaign=campaign, hypothesis_public_id=hypotheses[0].public_id, target_status=HypothesisStatus.REFUTED,
    )
    with pytest.raises(InvalidLifecycleTransitionError):
        service.transition_hypothesis(
            campaign=campaign, hypothesis_public_id=hypotheses[0].public_id, target_status=target,
        )


def test_transition_of_unknown_hypothesis_is_forbidden(strategy_campaign, db_session) -> None:
    campaign, _run, _stages = strategy_campaign
    with pytest.raises(ForbiddenError):
        StrategyService(db_session).transition_hypothesis(
            campaign=campaign, hypothesis_public_id="HYP-DOESNOTEXIST", target_status=HypothesisStatus.CONFIRMED,
        )


def test_transition_of_another_workspaces_hypothesis_is_forbidden(strategy_campaign, db_session) -> None:
    campaign_a, run_a, stages_a = strategy_campaign
    campaign_b, run_b, stages_b = build_campaign_run_with_stages(
        db_session, org_name="Org B2", workspace_name="WS B2", campaign_name="Campaign B2"
    )
    service = StrategyService(db_session)
    _strategy, _pos, hypotheses_b, _exps = service.record_strategy(
        campaign=campaign_b, campaign_run=run_b, stage_execution=stages_b[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.", hypotheses=[default_hypothesis()],
    )
    with pytest.raises(ForbiddenError):
        service.transition_hypothesis(
            campaign=campaign_a, hypothesis_public_id=hypotheses_b[0].public_id, target_status=HypothesisStatus.CONFIRMED,
        )


# --- experiment: no invented status semantics -----------------------------


def test_experiment_status_defaults_to_null_not_an_invented_value(strategy_campaign, db_session) -> None:
    campaign, run, stages = strategy_campaign
    _strategy, _pos, _hyps, experiments = StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.",
        hypotheses=[default_hypothesis(experiments=[default_experiment()])],
    )
    assert experiments[0].status is None


def test_experiment_status_column_is_a_plain_string_not_a_native_enum() -> None:
    column = Experiment.__table__.columns["status"]
    assert column.type.__class__.__name__ != "Enum"


# --- governance: no forbidden fields/entities/tables ----------------------


def test_no_status_or_approval_field_exists_on_strategy() -> None:
    forbidden = (
        "status", "confidence", "approval", "approved", "supersedes", "ready_for_planning",
        "maturity", "agent_id", "reasoning", "gate", "decision",
    )
    columns = [c.lower() for c in Strategy.__table__.columns.keys()]
    for term in forbidden:
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on Strategy"


def test_no_version_status_or_approval_field_exists_on_positioning() -> None:
    forbidden = ("version", "status", "confidence", "approval", "approved", "maturity", "agent_id", "reasoning")
    columns = [c.lower() for c in Positioning.__table__.columns.keys()]
    for term in forbidden:
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on Positioning"


def test_no_version_or_approval_field_exists_on_hypothesis() -> None:
    forbidden = ("version", "confidence", "approval", "approved", "fact", "learning", "agent_id", "reasoning")
    columns = [c.lower() for c in Hypothesis.__table__.columns.keys()]
    for term in forbidden:
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on Hypothesis"


def test_no_version_content_or_media_placeholder_field_exists_on_experiment() -> None:
    forbidden = (
        "version", "confidence", "approval", "approved", "content_piece_id", "paid_media_plan_id",
        "agent_id", "reasoning", "winner",
    )
    columns = [c.lower() for c in Experiment.__table__.columns.keys()]
    for term in forbidden:
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on Experiment"


def test_no_strategic_decision_or_approval_table_was_introduced() -> None:
    """BACKEND-14 has since authorized learning_candidates/
    strategic_recommendation_candidates (see tests/test_learning_domain.py)
    — this guard now covers only the tables that remain out of scope for
    every stage through BACKEND-14."""
    from app.persistence.base import metadata

    table_names = set(metadata.tables.keys())
    for forbidden_table in (
        "strategic_decisions", "strategy_approvals", "gate_decisions", "target_maturities", "positioning_maturities",
    ):
        assert forbidden_table not in table_names


def test_confirmed_hypothesis_does_not_create_a_learning_row(strategy_campaign, db_session) -> None:
    """HYPOTHESIS CONFIRMED != VALIDATED LEARNING (BACKEND-08 §10/§20) —
    confirming a Hypothesis must never create, reference, or imply any row
    outside the strategy module's own four tables. BACKEND-14 has since
    introduced learning_candidates, so this is now a row-count check
    rather than a table-existence check."""
    from sqlalchemy import func, select

    from app.learning.models import LearningCandidate

    campaign, run, stages = strategy_campaign
    service = StrategyService(db_session)
    _strategy, _pos, hypotheses, _exps = service.record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.", hypotheses=[default_hypothesis()],
    )
    candidates_before = db_session.execute(select(func.count()).select_from(LearningCandidate)).scalar_one()

    service.transition_hypothesis(
        campaign=campaign, hypothesis_public_id=hypotheses[0].public_id, target_status=HypothesisStatus.CONFIRMED,
    )

    candidates_after = db_session.execute(select(func.count()).select_from(LearningCandidate)).scalar_one()
    assert candidates_after == candidates_before


# --- stage-lifecycle non-mutation -----------------------------------------


def test_recording_strategy_leaves_run_and_stage_unchanged(strategy_campaign, db_session) -> None:
    from app.campaigns.models import CampaignRunStatus
    from app.orchestration.models import StageExecutionStatus

    campaign, run, stages = strategy_campaign
    StrategyService(db_session).record_strategy(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.STRATEGY],
        summary="Summary.", positioning_statement="Statement.",
    )
    db_session.refresh(run)
    assert run.status is CampaignRunStatus.CREATED
    for stage in stages.values():
        db_session.refresh(stage)
        assert stage.status is StageExecutionStatus.PENDING
