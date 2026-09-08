"""Audit attribution and atomicity for Research/Audience persistence
(BACKEND-07 §13/§14). All marked `postgres`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.audit.models import AuditEvent
from app.audit.repository import AuditEventRepository
from app.orchestration.models import BusinessStage
from app.research.models import AudienceProfile, ResearchReport, ResearchSource, VOCEvidence
from app.research.repository import ResearchSourceRepository
from app.research.service import ResearchService
from tests.researchtest import default_source, default_voc

pytestmark = pytest.mark.postgres


# --- exact attribution ------------------------------------------------


def test_report_recorded_event_identifies_the_exact_report(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    report, _ = ResearchService(db_session).record_report(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH],
        summary="v1", sources=[default_source()],
    )

    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "research.report.recorded")
    ).scalars().all()
    matching = [e for e in events if e.research_report_id == report.id]
    assert len(matching) == 1
    assert matching[0].audience_profile_id is None


def test_profile_recorded_event_identifies_the_exact_profile(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    profile, _ = ResearchService(db_session).record_audience_profile(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.AUDIENCE],
        summary="v1", voc_items=[default_voc()],
    )

    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "audience.profile.recorded")
    ).scalars().all()
    matching = [e for e in events if e.audience_profile_id == profile.id]
    assert len(matching) == 1
    assert matching[0].research_report_id is None


def test_two_report_versions_produce_distinguishable_events(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    service = ResearchService(db_session)
    report_1, _ = service.record_report(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH],
        summary="v1", sources=[],
    )
    report_2, _ = service.record_report(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH],
        summary="v2", sources=[],
    )

    events_1 = db_session.execute(
        select(AuditEvent).where(AuditEvent.research_report_id == report_1.id)
    ).scalars().all()
    events_2 = db_session.execute(
        select(AuditEvent).where(AuditEvent.research_report_id == report_2.id)
    ).scalars().all()

    assert len(events_1) == 1
    assert len(events_2) == 1
    assert events_1[0].id != events_2[0].id
    assert events_1[0].new_state == "v1"
    assert events_2[0].new_state == "v2"


def test_two_profile_versions_produce_distinguishable_events(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    service = ResearchService(db_session)
    profile_1, _ = service.record_audience_profile(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.AUDIENCE],
        summary="v1", voc_items=[],
    )
    profile_2, _ = service.record_audience_profile(
        campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.AUDIENCE],
        summary="v2", voc_items=[],
    )

    events_1 = db_session.execute(
        select(AuditEvent).where(AuditEvent.audience_profile_id == profile_1.id)
    ).scalars().all()
    events_2 = db_session.execute(
        select(AuditEvent).where(AuditEvent.audience_profile_id == profile_2.id)
    ).scalars().all()

    assert len(events_1) == 1
    assert len(events_2) == 1
    assert events_1[0].id != events_2[0].id


# --- atomicity: rollback on failure --------------------------------------


def _total_count(session, model) -> int:
    from sqlalchemy import func

    return session.execute(select(func.count()).select_from(model)).scalar_one()


def test_audit_event_failure_rolls_back_report_and_sources(research_campaign, db_session) -> None:
    """Counts total ResearchSource rows before/after (not an absolute
    zero) — other tests in the same full-suite run may have genuinely
    committed unrelated rows via a separate connection (see
    ``tests/test_research_api.py``'s helper), so an unfiltered "table is
    empty" assertion would be a false positive/negative depending on
    test order. The delta is what actually proves atomicity here."""
    campaign, run, stages = research_campaign
    service = ResearchService(db_session)
    sources_before = _total_count(db_session, ResearchSource)

    with patch.object(
        AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure before commit")
    ):
        with pytest.raises(RuntimeError, match="simulated audit failure before commit"):
            service.record_report(
                campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH],
                summary="should not persist", sources=[default_source()],
            )

    db_session.rollback()

    remaining_reports = db_session.execute(
        select(ResearchReport).where(ResearchReport.campaign_id == campaign.id)
    ).scalars().all()
    assert remaining_reports == [], "the report must not survive if its event failed to persist"
    assert _total_count(db_session, ResearchSource) == sources_before, "no source row must survive either"


def test_audit_event_failure_rolls_back_profile_and_voc(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    service = ResearchService(db_session)
    voc_before = _total_count(db_session, VOCEvidence)

    with patch.object(
        AuditEventRepository, "record", side_effect=RuntimeError("simulated audit failure before commit")
    ):
        with pytest.raises(RuntimeError, match="simulated audit failure before commit"):
            service.record_audience_profile(
                campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.AUDIENCE],
                summary="should not persist", voc_items=[default_voc()],
            )

    db_session.rollback()

    remaining_profiles = db_session.execute(
        select(AudienceProfile).where(AudienceProfile.campaign_id == campaign.id)
    ).scalars().all()
    assert remaining_profiles == [], "the profile must not survive if its event failed to persist"
    assert _total_count(db_session, VOCEvidence) == voc_before, "no VOC evidence row must survive either"


def test_child_source_failure_rolls_back_the_parent_report_too(research_campaign, db_session) -> None:
    campaign, run, stages = research_campaign
    service = ResearchService(db_session)

    with patch.object(
        ResearchSourceRepository, "create_many", side_effect=RuntimeError("simulated source failure")
    ):
        with pytest.raises(RuntimeError, match="simulated source failure"):
            service.record_report(
                campaign=campaign, campaign_run=run, stage_execution=stages[BusinessStage.RESEARCH],
                summary="should not persist", sources=[default_source()],
            )

    db_session.rollback()

    remaining_reports = db_session.execute(
        select(ResearchReport).where(ResearchReport.campaign_id == campaign.id)
    ).scalars().all()
    assert remaining_reports == [], "a report must never exist without its (attempted) sources having succeeded too"
