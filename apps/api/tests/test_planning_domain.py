"""Planning domain persistence, provenance, versioning, tenancy, and
governance-boundary tests (BACKEND-09). All marked `postgres`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.campaigns.repository import CampaignRepository, CampaignRunRepository
from app.core.api_errors import ProvenanceMismatchError, VersionConflictError
from app.orchestration.models import BusinessStage
from app.orchestration.repository import RunStageExecutionRepository
from app.planning.models import ContentPlan, PlanItem
from app.planning.repository import ContentPlanRepository
from app.planning.service import PlanningService
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.planningtest import default_plan_item
from tests.researchtest import build_campaign_run_with_stages

pytestmark = pytest.mark.postgres


# --- domain persistence ------------------------------------------------


def test_content_plan_and_plan_items_persist(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    service = PlanningService(db_session)

    plan, items = service.record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN],
        summary="A two-week content calendar introducing the training method.",
        items=[default_plan_item(), default_plan_item(format="Carrusel", sequence=2)],
    )

    assert plan.public_id.startswith("PLN-")
    assert plan.version == 1
    assert len(items) == 2
    assert all(i.public_id.startswith("ITM-") for i in items)
    assert all(i.content_plan_id == plan.id for i in items)


def test_plan_may_be_created_with_no_items(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    plan, items = PlanningService(db_session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN],
        summary="Summary.",
    )
    assert plan is not None
    assert items == []


def test_plan_item_has_no_workspace_id_column() -> None:
    assert "workspace_id" not in PlanItem.__table__.columns.keys()


def test_scheduled_date_persists_and_is_nullable(planning_campaign, db_session) -> None:
    import datetime

    campaign, run, stages = planning_campaign
    plan, items = PlanningService(db_session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN],
        summary="Summary.",
        items=[
            default_plan_item(scheduled_date=datetime.date(2026, 1, 15)),
            default_plan_item(sequence=2, scheduled_date=None),
        ],
    )
    assert items[0].scheduled_date == datetime.date(2026, 1, 15)
    assert items[1].scheduled_date is None


def test_sequence_persists_as_given(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    _plan, items = PlanningService(db_session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN],
        summary="Summary.",
        items=[default_plan_item(sequence=3), default_plan_item(sequence=1)],
    )
    assert {i.sequence for i in items} == {1, 3}


# --- provenance: valid -------------------------------------------------


def test_plan_provenance_matches_campaign_run_and_stage(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    plan, _items = PlanningService(db_session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN],
        summary="Summary.",
    )
    assert plan.campaign_id == campaign.id
    assert plan.campaign_run_id == run.id
    assert plan.stage_execution_id == stages[BusinessStage.PLAN].id


# --- provenance: invalid -------------------------------------------------


def test_cross_campaign_campaign_run_is_rejected(db_session) -> None:
    campaign_a, run_a, stages_a = build_campaign_run_with_stages(db_session, campaign_name="Campaign A")
    campaign_b, _run_b, _stages_b = build_campaign_run_with_stages(db_session, campaign_name="Campaign B")

    with pytest.raises(ProvenanceMismatchError):
        PlanningService(db_session).record_plan(
            campaign=campaign_b, campaign_run=run_a, stage_execution=stages_a[BusinessStage.PLAN],
            summary="Should be rejected.",
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
        PlanningService(db_session).record_plan(
            campaign=campaign_b, campaign_run=run_a, stage_execution=stages_a[BusinessStage.PLAN],
            summary="Should be rejected.",
        )


def test_stage_execution_from_a_different_run_is_rejected(db_session) -> None:
    campaign_a, run_a, _stages_a = build_campaign_run_with_stages(db_session, campaign_name="Campaign A")
    campaign_b, run_b, stages_b = build_campaign_run_with_stages(db_session, campaign_name="Campaign B")

    with pytest.raises(ProvenanceMismatchError):
        PlanningService(db_session).record_plan(
            campaign=campaign_a, campaign_run=run_a, stage_execution=stages_b[BusinessStage.PLAN],
            summary="Should be rejected.",
        )


def test_non_plan_stage_is_rejected(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    with pytest.raises(ProvenanceMismatchError):
        PlanningService(db_session).record_plan(
            campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH],
            summary="Should be rejected.",
        )


def test_database_rejects_a_plan_with_mismatched_workspace(db_session) -> None:
    """Bypasses the service entirely — direct model construction proves
    the composite FK itself rejects a workspace mismatch."""
    campaign, run, stages = build_campaign_run_with_stages(db_session)
    other_workspace = WorkspaceRepository(db_session).create(
        organization_id=OrganizationRepository(db_session).create(name="Rogue Org").id, name="Rogue WS"
    )
    db_session.flush()

    rogue_plan = ContentPlan(
        public_id="PLN-MISMATCHTEST",
        workspace_id=other_workspace.id,
        campaign_id=campaign.id,
        campaign_run_id=run.id,
        stage_execution_id=stages[BusinessStage.PLAN].id,
        version=1,
        summary="Should be rejected at the DB level.",
    )
    db_session.add(rogue_plan)
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- tenancy: via-parent only for Plan Item -----------------------------


def test_plan_item_tenancy_is_derived_via_parent_plan(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    plan, items = PlanningService(db_session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN],
        summary="Summary.", items=[default_plan_item()],
    )
    reloaded_plan = db_session.execute(select(ContentPlan).where(ContentPlan.id == items[0].content_plan_id)).scalar_one()
    assert reloaded_plan.workspace_id == campaign.workspace_id


def test_plan_item_orphaned_from_nonexistent_plan_is_rejected(db_session) -> None:
    import uuid

    rogue = PlanItem(
        public_id="ITM-ORPHANTEST",
        content_plan_id=uuid.uuid4(),
        format="Reel",
        objective="Should be rejected.",
        sequence=1,
    )
    db_session.add(rogue)
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- versioning ----------------------------------------------------------


def test_versions_are_unique_per_campaign(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    service = PlanningService(db_session)
    first, _items = service.record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="v1",
    )
    assert first.version == 1

    second, _items = service.record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="v2",
    )
    assert second.version == 2
    assert first.id != second.id


def test_duplicate_version_is_rejected_at_the_database_level(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    db_session.add(
        ContentPlan(
            public_id="PLN-DUPTEST0001", workspace_id=campaign.workspace_id, campaign_id=campaign.id,
            campaign_run_id=run.id, stage_execution_id=stages[BusinessStage.PLAN].id,
            version=1, summary="first",
        )
    )
    db_session.flush()
    db_session.add(
        ContentPlan(
            public_id="PLN-DUPTEST0002", workspace_id=campaign.workspace_id, campaign_id=campaign.id,
            campaign_run_id=run.id, stage_execution_id=stages[BusinessStage.PLAN].id,
            version=1, summary="duplicate",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_service_maps_version_conflict_to_a_deterministic_error(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    service = PlanningService(db_session)
    service.record_plan(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="v1")
    with patch.object(ContentPlanRepository, "next_version_for_campaign", return_value=1):
        with pytest.raises(VersionConflictError):
            service.record_plan(
                campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN],
                summary="racing write",
            )


def test_current_plan_is_the_highest_version(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    service = PlanningService(db_session)
    service.record_plan(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="v1")
    service.record_plan(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="v2")
    latest, _items = service.get_plan_output(campaign=campaign)
    assert latest.version == 2
    assert latest.summary == "v2"


def test_old_version_remains_persisted_after_a_new_one_is_created(planning_campaign, db_session) -> None:
    campaign, run, stages = planning_campaign
    service = PlanningService(db_session)
    first, _items = service.record_plan(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="v1")
    service.record_plan(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="v2")

    reloaded = db_session.execute(select(ContentPlan).where(ContentPlan.id == first.id)).scalar_one()
    assert reloaded.summary == "v1"
    assert reloaded.version == 1


def test_two_campaign_runs_do_not_overwrite_each_others_plans(db_session) -> None:
    organization = OrganizationRepository(db_session).create(name="History Org P")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="History WS P")
    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="History Campaign P")
    run_1 = CampaignRunRepository(db_session).create(campaign=campaign, run_number=1)
    db_session.flush()
    stages_1 = RunStageExecutionRepository(db_session).materialize_for_run(campaign_run=run_1)
    run_2 = CampaignRunRepository(db_session).create(campaign=campaign, run_number=2)
    db_session.flush()
    stages_2 = RunStageExecutionRepository(db_session).materialize_for_run(campaign_run=run_2)

    service = PlanningService(db_session)
    plan_1, _items = service.record_plan(
        campaign=campaign, campaign_run=run_1,
        stage_execution=next(s for s in stages_1 if s.stage is BusinessStage.PLAN),
        summary="From run 1.",
    )
    plan_2, _items = service.record_plan(
        campaign=campaign, campaign_run=run_2,
        stage_execution=next(s for s in stages_2 if s.stage is BusinessStage.PLAN),
        summary="From run 2.",
    )
    assert plan_1.version == 1
    assert plan_2.version == 2
    assert plan_1.campaign_run_id == run_1.id
    assert plan_2.campaign_run_id == run_2.id
    stored = db_session.execute(select(ContentPlan).where(ContentPlan.campaign_id == campaign.id)).scalars().all()
    assert len(stored) == 2


# --- Plan Item mutability: acknowledged, not implemented ------------------


def test_plan_item_has_no_status_or_mutation_columns() -> None:
    forbidden = ("status", "approved", "briefed", "ready_for_production", "updated_at")
    columns = [c.lower() for c in PlanItem.__table__.columns.keys()]
    for term in forbidden:
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on PlanItem"


def test_planning_service_exposes_no_mutation_method() -> None:
    forbidden_methods = ("update_plan_item", "transition_plan_item", "mark_briefed", "lock_plan_item", "patch_plan_item")
    for method in forbidden_methods:
        assert not hasattr(PlanningService, method), f"unexpected mutation method {method!r} on PlanningService"


# --- governance: no forbidden fields/entities/tables ----------------------


def test_no_status_or_approval_field_exists_on_content_plan() -> None:
    forbidden = (
        "status", "confidence", "approval", "approved", "ready_for_production", "ready_for_planning",
        "maturity", "agent_id", "reasoning", "gate", "decision", "strategy",
    )
    columns = [c.lower() for c in ContentPlan.__table__.columns.keys()]
    for term in forbidden:
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on ContentPlan"


def test_no_forbidden_fields_exist_on_plan_item() -> None:
    forbidden = (
        "channel", "funnel", "cta", "content_function", "status", "approved", "ready_for_production",
        "briefed", "content_brief_id", "content_piece_id", "strategy_id", "hypothesis_id", "experiment_id",
        "agent_id", "reasoning", "workspace_id",
    )
    columns = [c.lower() for c in PlanItem.__table__.columns.keys()]
    for term in forbidden:
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on PlanItem"


def test_no_content_brief_content_piece_or_distribution_table_was_introduced() -> None:
    from app.persistence.base import metadata

    table_names = set(metadata.tables.keys())
    for forbidden_table in (
        "content_briefs", "content_pieces", "content_versions", "content_approvals", "creative_briefs",
        "assets", "distribution_plans", "distribution_readiness", "paid_media_plans", "paid_scopes",
    ):
        assert forbidden_table not in table_names


def test_content_plan_has_no_structural_strategy_reference() -> None:
    """STRATEGY != CONTENT PLAN — BACKEND-01 defines no FK from Content Plan
    to Strategy/Positioning/Hypothesis/Experiment; this is a documented,
    carried-forward traceability gap, not repaired here."""
    columns = [c.lower() for c in ContentPlan.__table__.columns.keys()]
    for term in ("strategy", "positioning", "hypothesis", "experiment"):
        assert not any(term in c for c in columns)


# --- stage-lifecycle non-mutation -----------------------------------------


def test_recording_plan_leaves_run_and_stage_unchanged(planning_campaign, db_session) -> None:
    from app.campaigns.models import CampaignRunStatus
    from app.orchestration.models import StageExecutionStatus

    campaign, run, stages = planning_campaign
    PlanningService(db_session).record_plan(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.PLAN], summary="Summary.",
    )
    db_session.refresh(run)
    assert run.status is CampaignRunStatus.CREATED
    for stage in stages.values():
        db_session.refresh(stage)
        assert stage.status is StageExecutionStatus.PENDING
