"""Research/Audience persistence — BACKEND-07.

No public HTTP write endpoint exists (BACKEND-07 §15) — BACKEND-01's own
API map defines `/campaigns/{id}/research` and `/campaigns/{id}/audience`
as GET-only. This service is the controlled, service-layer-only
mechanism a future runtime (once real Agent Run/Gate Decision exist)
will call to persist output; tests call it directly today, exactly the
same shape as ``OrchestrationService.create_decision_request`` in
BACKEND-06.

Every write method here follows the established transaction-ownership
pattern: validate provenance, create the parent + all children + exactly
one AuditEvent, call ``self.session.commit()`` once at the end. If
anything raises before that commit — including the AuditEvent's own
``flush()`` — nothing persists (BACKEND-07 §12/§14).

PERSISTING RESEARCH/AUDIENCE OUTPUT NEVER MUTATES CampaignRun.status,
RunStageExecution.status, or any HumanDecisionRequest/Response
(BACKEND-07 §17) — no code below touches any of those tables.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import ActorType
from app.audit.repository import AuditEventRepository
from app.campaigns.models import Campaign, CampaignRun
from app.core.api_errors import ProvenanceMismatchError, VersionConflictError
from app.orchestration.models import BusinessStage, RunStageExecution
from app.research.models import AudienceProfile, ResearchReport, ResearchSource, VOCEvidence
from app.research.repository import (
    AudienceProfileRepository,
    ResearchReportRepository,
    ResearchSourceRepository,
    VOCEvidenceRepository,
)

EVENT_REPORT_RECORDED = "research.report.recorded"
EVENT_PROFILE_RECORDED = "audience.profile.recorded"


def _validate_provenance(
    *, campaign: Campaign, campaign_run: CampaignRun, stage_execution: RunStageExecution, expected_stage: BusinessStage
) -> None:
    """BACKEND-07 §10: a plain/composite FK alone cannot prove this —
    each check is explicit here. Any failure raises the same
    ``ProvenanceMismatchError``, never revealing which specific check
    failed (mirrors ``ForbiddenError``'s own non-leaky precedent)."""
    if campaign_run.campaign_id != campaign.id:
        raise ProvenanceMismatchError()
    if campaign_run.workspace_id != campaign.workspace_id:
        raise ProvenanceMismatchError()
    if stage_execution.campaign_run_id != campaign_run.id:
        raise ProvenanceMismatchError()
    if stage_execution.stage is not expected_stage:
        raise ProvenanceMismatchError()


class ResearchService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.reports = ResearchReportRepository(session)
        self.sources = ResearchSourceRepository(session)
        self.profiles = AudienceProfileRepository(session)
        self.voc = VOCEvidenceRepository(session)
        self.events = AuditEventRepository(session)

    # --- reads (GET-only public surface calls these) -----------------

    def get_research_output(self, *, campaign: Campaign) -> tuple[ResearchReport | None, list[ResearchSource]]:
        report = self.reports.get_current_for_campaign(campaign.id)
        if report is None:
            return None, []
        return report, self.sources.list_for_report(report.id)

    def get_audience_output(self, *, campaign: Campaign) -> tuple[AudienceProfile | None, list[VOCEvidence]]:
        profile = self.profiles.get_current_for_campaign(campaign.id)
        if profile is None:
            return None, []
        return profile, self.voc.list_for_profile(profile.id)

    # --- writes (service-layer only; no public HTTP trigger) ---------

    def record_report(
        self,
        *,
        campaign: Campaign,
        campaign_run: CampaignRun,
        stage_execution: RunStageExecution,
        summary: str,
        sources: list[dict],
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> tuple[ResearchReport, list[ResearchSource]]:
        _validate_provenance(
            campaign=campaign, campaign_run=campaign_run, stage_execution=stage_execution,
            expected_stage=BusinessStage.RESEARCH,
        )
        next_version = self.reports.next_version_for_campaign(campaign.id)

        # Only the parent-row insert is version-conflict-sensitive — the
        # DB's (campaign_id, version) unique constraint is the
        # authoritative concurrency guard (BACKEND-07 §11). Child-row or
        # event failures below are a *different* kind of problem (e.g. a
        # check-constraint violation) and must propagate as themselves,
        # not be misreported as a version race.
        try:
            report = self.reports.create(
                campaign=campaign, campaign_run=campaign_run, stage_execution=stage_execution,
                version=next_version, summary=summary,
            )
        except IntegrityError:
            self.session.rollback()
            raise VersionConflictError() from None

        # Nothing here calls commit() until every child row and the
        # AuditEvent have all succeeded — if anything below raises, the
        # caller's own session-lifecycle wrapper (`get_db()` in
        # production, or the test's own rollback) discards the parent
        # row too, since it was only flushed, never committed
        # (BACKEND-07 §12).
        created_sources = self.sources.create_many(research_report=report, sources=sources)
        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_REPORT_RECORDED,
            actor_type=ActorType.USER if actor_user_id is not None else ActorType.SYSTEM,
            campaign_id=campaign.id,
            campaign_run_id=campaign_run.id,
            stage_execution_id=stage_execution.id,
            research_report_id=report.id,
            new_state=f"v{report.version}",
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()

        return report, created_sources

    def record_audience_profile(
        self,
        *,
        campaign: Campaign,
        campaign_run: CampaignRun,
        stage_execution: RunStageExecution,
        summary: str,
        voc_items: list[dict],
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> tuple[AudienceProfile, list[VOCEvidence]]:
        _validate_provenance(
            campaign=campaign, campaign_run=campaign_run, stage_execution=stage_execution,
            expected_stage=BusinessStage.AUDIENCE,
        )
        next_version = self.profiles.next_version_for_campaign(campaign.id)

        try:
            profile = self.profiles.create(
                campaign=campaign, campaign_run=campaign_run, stage_execution=stage_execution,
                version=next_version, summary=summary,
            )
        except IntegrityError:
            self.session.rollback()
            raise VersionConflictError() from None

        created_voc = self.voc.create_many(audience_profile=profile, items=voc_items)
        self.events.record(
            workspace_id=campaign.workspace_id,
            event_type=EVENT_PROFILE_RECORDED,
            actor_type=ActorType.USER if actor_user_id is not None else ActorType.SYSTEM,
            campaign_id=campaign.id,
            campaign_run_id=campaign_run.id,
            stage_execution_id=stage_execution.id,
            audience_profile_id=profile.id,
            new_state=f"v{profile.version}",
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.commit()

        return profile, created_voc
