"""Research/Audience domain persistence, provenance, versioning, and
evidence-integrity tests (BACKEND-07 §5-§11/§18). All marked `postgres`.
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
from app.research.models import AudienceProfile, ResearchReport, ResearchSource, VOCEvidence
from app.research.repository import ResearchReportRepository
from app.research.service import ResearchService
from app.workspaces.repository import OrganizationRepository, WorkspaceRepository
from tests.researchtest import build_campaign_run_with_stages, default_source, default_voc

pytestmark = pytest.mark.postgres


# --- domain persistence ------------------------------------------------


def test_research_report_and_sources_persist(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    service = ResearchService(db_session)

    report, sources = service.record_report(
        campaign=campaign,
        campaign_run=run,
        stage_execution=stages[BusinessStage.RESEARCH],
        summary="Owners want a clear, progressive method for home training.",
        sources=[default_source(), default_source(title="Second source")],
    )

    assert report.public_id.startswith("RPT-")
    assert report.version == 1
    assert len(sources) == 2
    assert all(s.public_id.startswith("SRCE-") for s in sources)
    assert all(s.research_report_id == report.id for s in sources)


def test_audience_profile_and_voc_persist(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    service = ResearchService(db_session)

    profile, voc_items = service.record_audience_profile(
        campaign=campaign,
        campaign_run=run,
        stage_execution=stages[BusinessStage.AUDIENCE],
        summary="Primarily first-time dog owners seeking a structured method.",
        voc_items=[default_voc(), default_voc(verbatim_quote=None, paraphrase="Wants clear step-by-step guidance.")],
    )

    assert profile.public_id.startswith("AUD-")
    assert profile.version == 1
    assert len(voc_items) == 2
    assert all(v.public_id.startswith("VOC-") for v in voc_items)
    assert all(v.audience_profile_id == profile.id for v in voc_items)


# --- provenance: valid -------------------------------------------------


def test_report_provenance_matches_campaign_run_and_stage(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    report, _ = ResearchService(db_session).record_report(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH],
        summary="Findings.", sources=[default_source()],
    )
    assert report.campaign_id == campaign.id
    assert report.campaign_run_id == run.id
    assert report.stage_execution_id == stages[BusinessStage.RESEARCH].id


def test_profile_provenance_matches_campaign_run_and_stage(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    profile, _ = ResearchService(db_session).record_audience_profile(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.AUDIENCE],
        summary="Audience.", voc_items=[default_voc()],
    )
    assert profile.campaign_id == campaign.id
    assert profile.campaign_run_id == run.id
    assert profile.stage_execution_id == stages[BusinessStage.AUDIENCE].id


# --- provenance: invalid -------------------------------------------------


def test_cross_campaign_campaign_run_is_rejected(db_session) -> None:
    campaign_a, run_a, stages_a = build_campaign_run_with_stages(db_session, campaign_name="Campaign A")
    campaign_b, _run_b, _stages_b = build_campaign_run_with_stages(db_session, campaign_name="Campaign B")

    with pytest.raises(ProvenanceMismatchError):
        ResearchService(db_session).record_report(
            campaign=campaign_b,  # campaign B, but run belongs to campaign A
            campaign_run=run_a,
            stage_execution=stages_a[BusinessStage.RESEARCH],
            summary="Should be rejected.",
            sources=[],
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
        ResearchService(db_session).record_report(
            campaign=campaign_b,
            campaign_run=run_a,  # run belongs to a different workspace entirely
            stage_execution=stages_a[BusinessStage.RESEARCH],
            summary="Should be rejected.",
            sources=[],
        )


def test_stage_execution_from_a_different_run_is_rejected(db_session) -> None:
    campaign_a, run_a, _stages_a = build_campaign_run_with_stages(db_session, campaign_name="Campaign A")
    campaign_b, run_b, stages_b = build_campaign_run_with_stages(db_session, campaign_name="Campaign B")

    with pytest.raises(ProvenanceMismatchError):
        ResearchService(db_session).record_report(
            campaign=campaign_a,
            campaign_run=run_a,
            stage_execution=stages_b[BusinessStage.RESEARCH],  # belongs to run_b, not run_a
            summary="Should be rejected.",
            sources=[],
        )


def test_audience_stage_rejected_for_research_report(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    with pytest.raises(ProvenanceMismatchError):
        ResearchService(db_session).record_report(
            campaign=campaign, campaign_run=run,
            stage_execution=stages[BusinessStage.AUDIENCE],  # wrong stage type
            summary="Should be rejected.", sources=[],
        )


def test_research_stage_rejected_for_audience_profile(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    with pytest.raises(ProvenanceMismatchError):
        ResearchService(db_session).record_audience_profile(
            campaign=campaign, campaign_run=run,
            stage_execution=stages[BusinessStage.RESEARCH],  # wrong stage type
            summary="Should be rejected.", voc_items=[],
        )


def test_database_rejects_a_report_with_mismatched_workspace(db_session) -> None:
    """Bypasses the service entirely — direct model construction proves
    the composite FK itself rejects a workspace mismatch, independent of
    application-level validation."""
    campaign, run, stages = build_campaign_run_with_stages(db_session)
    other_workspace = WorkspaceRepository(db_session).create(
        organization_id=OrganizationRepository(db_session).create(name="Rogue Org").id, name="Rogue WS"
    )
    db_session.flush()

    rogue_report = ResearchReport(
        public_id="RPT-MISMATCHTEST",
        workspace_id=other_workspace.id,  # deliberately wrong
        campaign_id=campaign.id,
        campaign_run_id=run.id,
        stage_execution_id=stages[BusinessStage.RESEARCH].id,
        version=1,
        summary="Should be rejected at the DB level.",
    )
    db_session.add(rogue_report)
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- versioning ----------------------------------------------------------


def test_versions_are_unique_per_campaign(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    service = ResearchService(db_session)
    first, _ = service.record_report(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH],
        summary="v1", sources=[],
    )
    assert first.version == 1

    second, _ = service.record_report(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH],
        summary="v2", sources=[],
    )
    assert second.version == 2
    assert first.id != second.id


def test_duplicate_version_is_rejected_at_the_database_level(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    db_session.add(
        ResearchReport(
            public_id="RPT-DUPTEST0001",
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            campaign_run_id=run.id,
            stage_execution_id=stages[BusinessStage.RESEARCH].id,
            version=1,
            summary="first",
        )
    )
    db_session.flush()

    db_session.add(
        ResearchReport(
            public_id="RPT-DUPTEST0002",
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            campaign_run_id=run.id,
            stage_execution_id=stages[BusinessStage.RESEARCH].id,
            version=1,  # duplicate
            summary="duplicate",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_service_maps_version_conflict_to_a_deterministic_error(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    service = ResearchService(db_session)
    service.record_report(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH],
        summary="v1", sources=[],
    )
    # Force the repository's own version computation to go stale by
    # inserting version 2 directly, then trying the service again with
    # a version number it will (incorrectly) still think is free.
    with patch.object(ResearchReportRepository, "next_version_for_campaign", return_value=1):
        with pytest.raises(VersionConflictError):
            service.record_report(
                campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH],
                summary="racing write", sources=[],
            )


def test_two_campaign_runs_do_not_overwrite_each_others_reports(db_session) -> None:
    """Historical integrity (BACKEND-06 §6/BACKEND-07 §3): a second
    CampaignRun on the same Campaign must never overwrite the first
    run's evidence — both remain queryable by their own campaign_run_id."""
    organization = OrganizationRepository(db_session).create(name="History Org")
    workspace = WorkspaceRepository(db_session).create(organization_id=organization.id, name="History WS")
    from app.campaigns.repository import CampaignRepository

    campaign = CampaignRepository(db_session).create(workspace_id=workspace.id, name="History Campaign")
    run_1 = CampaignRunRepository(db_session).create(campaign=campaign, run_number=1)
    db_session.flush()
    stages_1 = RunStageExecutionRepository(db_session).materialize_for_run(campaign_run=run_1)
    run_2 = CampaignRunRepository(db_session).create(campaign=campaign, run_number=2)
    db_session.flush()
    stages_2 = RunStageExecutionRepository(db_session).materialize_for_run(campaign_run=run_2)

    service = ResearchService(db_session)
    report_1, _ = service.record_report(
        campaign=campaign, campaign_run=run_1,
        stage_execution=next(s for s in stages_1 if s.stage is BusinessStage.RESEARCH),
        summary="From run 1.", sources=[],
    )
    report_2, _ = service.record_report(
        campaign=campaign, campaign_run=run_2,
        stage_execution=next(s for s in stages_2 if s.stage is BusinessStage.RESEARCH),
        summary="From run 2.", sources=[],
    )

    assert report_1.version == 1
    assert report_2.version == 2
    assert report_1.campaign_run_id == run_1.id
    assert report_2.campaign_run_id == run_2.id
    # Both rows remain persisted — nothing overwritten.
    stored = db_session.execute(
        select(ResearchReport).where(ResearchReport.campaign_id == campaign.id)
    ).scalars().all()
    assert len(stored) == 2


def test_current_report_is_the_highest_version(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    service = ResearchService(db_session)
    service.record_report(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH], summary="v1", sources=[])
    service.record_report(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH], summary="v2", sources=[])
    latest, _ = service.get_research_output(campaign=campaign)
    assert latest.version == 2
    assert latest.summary == "v2"


def test_old_version_remains_persisted_after_a_new_one_is_created(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    service = ResearchService(db_session)
    first, _ = service.record_report(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH], summary="v1", sources=[])
    service.record_report(campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH], summary="v2", sources=[])

    reloaded = db_session.execute(select(ResearchReport).where(ResearchReport.id == first.id)).scalar_one()
    assert reloaded.summary == "v1"
    assert reloaded.version == 1


# --- VOC integrity ---------------------------------------------------------


def test_voc_verbatim_and_paraphrase_are_preserved_distinctly(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    _profile, voc_items = ResearchService(db_session).record_audience_profile(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.AUDIENCE],
        summary="Audience.",
        voc_items=[default_voc(verbatim_quote="Exact words here.", paraphrase="An analyst's own restatement.")],
    )
    assert voc_items[0].verbatim_quote == "Exact words here."
    assert voc_items[0].paraphrase == "An analyst's own restatement."
    assert voc_items[0].verbatim_quote != voc_items[0].paraphrase


def test_voc_rejects_both_verbatim_and_paraphrase_null(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    with pytest.raises(IntegrityError):
        ResearchService(db_session).record_audience_profile(
            campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.AUDIENCE],
            summary="Audience.",
            voc_items=[default_voc(verbatim_quote=None, paraphrase=None)],
        )


def test_voc_rejects_semantically_empty_whitespace_only_input(research_campaign, db_session) -> None:
    """Whitespace-only strings must be normalized to NULL before the
    check constraint is evaluated — otherwise the constraint could be
    bypassed with meaningless content (BACKEND-07 §8)."""
    campaign, run, stages = research_campaign
    with pytest.raises(IntegrityError):
        ResearchService(db_session).record_audience_profile(
            campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.AUDIENCE],
            summary="Audience.",
            voc_items=[default_voc(verbatim_quote="   ", paraphrase="\t\n")],
        )


def test_voc_provenance_fields_are_preserved(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    _profile, voc_items = ResearchService(db_session).record_audience_profile(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.AUDIENCE],
        summary="Audience.",
        voc_items=[default_voc(source_type="FORUM", source_locator="https://example.com/forum/1")],
    )
    assert voc_items[0].source_type.value == "FORUM"
    assert voc_items[0].source_locator == "https://example.com/forum/1"


# --- evidence integrity: no forbidden fields/entities ----------------------


def test_no_truth_reliability_or_approval_field_exists_on_research_source() -> None:
    forbidden = ("status", "confidence", "reliability", "approval", "truth", "agent_id", "reasoning")
    columns = [c.lower() for c in ResearchSource.__table__.columns.keys()]
    for term in forbidden:
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on ResearchSource"


def test_no_status_or_target_approval_field_exists_on_audience_profile() -> None:
    forbidden = (
        "status", "confidence", "approval", "is_target", "primary_target", "approved_target",
        "positioning", "strategic", "agent_id", "reasoning",
    )
    columns = [c.lower() for c in AudienceProfile.__table__.columns.keys()]
    for term in forbidden:
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on AudienceProfile"


def test_no_status_field_exists_on_research_report() -> None:
    forbidden = ("status", "confidence", "approval", "supersedes", "agent_id", "reasoning")
    columns = [c.lower() for c in ResearchReport.__table__.columns.keys()]
    for term in forbidden:
        assert not any(term in c for c in columns), f"unexpected field containing {term!r} on ResearchReport"


def test_no_finding_or_insight_table_was_introduced() -> None:
    from app.persistence.base import metadata

    table_names = set(metadata.tables.keys())
    assert "research_findings" not in table_names
    assert "audience_insights" not in table_names
    assert "audience_analyses" not in table_names


def test_voc_evidence_has_no_direct_fk_to_research_source() -> None:
    """BACKEND-01's ER diagram draws no edge between VOC Evidence and
    Source Reference — confirmed no such column exists."""
    columns = VOCEvidence.__table__.columns.keys()
    assert "research_source_id" not in columns
