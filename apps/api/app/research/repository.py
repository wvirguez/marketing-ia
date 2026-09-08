"""Data access for ResearchReport / ResearchSource / AudienceProfile /
VOCEvidence. No repository here calls ``session.commit()`` — see
``app/persistence/session.py`` and ``app/research/service.py`` for the
transaction-ownership boundary.

Every ``create`` method takes the parent domain object(s) (``Campaign``,
``CampaignRun``, ``RunStageExecution``), never a raw ``workspace_id``/
``campaign_id``/``campaign_run_id`` parameter — the same "no independent
parameter, no possibility of drift" pattern established in
``app/campaigns/repository.py``/``app/orchestration/repository.py``,
applied here too.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign, CampaignRun
from app.core.ids import generate_public_id
from app.orchestration.models import RunStageExecution
from app.research.models import AudienceProfile, ResearchReport, ResearchSource, SourceType, VOCEvidence


def _normalize_optional_text(value: str | None) -> str | None:
    """Blank/whitespace-only input is treated as absent — never persisted
    as a meaningless empty string (BACKEND-07 §8/§19)."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class ResearchReportRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        campaign: Campaign,
        campaign_run: CampaignRun,
        stage_execution: RunStageExecution,
        version: int,
        summary: str,
    ) -> ResearchReport:
        report = ResearchReport(
            public_id=generate_public_id("RPT"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            campaign_run_id=campaign_run.id,
            stage_execution_id=stage_execution.id,
            version=version,
            summary=summary,
        )
        self.session.add(report)
        self.session.flush()
        return report

    def get_by_public_id(self, public_id: str) -> ResearchReport | None:
        return self.session.execute(
            select(ResearchReport).where(ResearchReport.public_id == public_id)
        ).scalar_one_or_none()

    def get_current_for_campaign(self, campaign_id: uuid.UUID) -> ResearchReport | None:
        return self.session.execute(
            select(ResearchReport)
            .where(ResearchReport.campaign_id == campaign_id)
            .order_by(ResearchReport.version.desc())
            .limit(1)
        ).scalar_one_or_none()

    def next_version_for_campaign(self, campaign_id: uuid.UUID) -> int:
        current = self.session.execute(
            select(func.max(ResearchReport.version)).where(ResearchReport.campaign_id == campaign_id)
        ).scalar_one()
        return (current or 0) + 1


class ResearchSourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_many(self, *, research_report: ResearchReport, sources: list[dict]) -> list[ResearchSource]:
        rows = [
            ResearchSource(
                public_id=generate_public_id("SRCE"),
                research_report_id=research_report.id,
                source_type=SourceType(item["source_type"]) if isinstance(item["source_type"], str) else item["source_type"],
                title=item["title"],
                locator=_normalize_optional_text(item.get("locator")),
                publisher=_normalize_optional_text(item.get("publisher")),
                excerpt=_normalize_optional_text(item.get("excerpt")),
                retrieved_at=item.get("retrieved_at"),
            )
            for item in sources
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def list_for_report(self, research_report_id: uuid.UUID) -> list[ResearchSource]:
        return list(
            self.session.execute(
                select(ResearchSource)
                .where(ResearchSource.research_report_id == research_report_id)
                .order_by(ResearchSource.created_at.asc(), ResearchSource.id.asc())
            )
            .scalars()
            .all()
        )


class AudienceProfileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        campaign: Campaign,
        campaign_run: CampaignRun,
        stage_execution: RunStageExecution,
        version: int,
        summary: str,
    ) -> AudienceProfile:
        profile = AudienceProfile(
            public_id=generate_public_id("AUD"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            campaign_run_id=campaign_run.id,
            stage_execution_id=stage_execution.id,
            version=version,
            summary=summary,
        )
        self.session.add(profile)
        self.session.flush()
        return profile

    def get_by_public_id(self, public_id: str) -> AudienceProfile | None:
        return self.session.execute(
            select(AudienceProfile).where(AudienceProfile.public_id == public_id)
        ).scalar_one_or_none()

    def get_current_for_campaign(self, campaign_id: uuid.UUID) -> AudienceProfile | None:
        return self.session.execute(
            select(AudienceProfile)
            .where(AudienceProfile.campaign_id == campaign_id)
            .order_by(AudienceProfile.version.desc())
            .limit(1)
        ).scalar_one_or_none()

    def next_version_for_campaign(self, campaign_id: uuid.UUID) -> int:
        current = self.session.execute(
            select(func.max(AudienceProfile.version)).where(AudienceProfile.campaign_id == campaign_id)
        ).scalar_one()
        return (current or 0) + 1


class VOCEvidenceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_many(self, *, audience_profile: AudienceProfile, items: list[dict]) -> list[VOCEvidence]:
        rows = [
            VOCEvidence(
                public_id=generate_public_id("VOC"),
                audience_profile_id=audience_profile.id,
                verbatim_quote=_normalize_optional_text(item.get("verbatim_quote")),
                paraphrase=_normalize_optional_text(item.get("paraphrase")),
                source_type=SourceType(item["source_type"]) if item.get("source_type") else None,
                source_locator=_normalize_optional_text(item.get("source_locator")),
            )
            for item in items
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def list_for_profile(self, audience_profile_id: uuid.UUID) -> list[VOCEvidence]:
        return list(
            self.session.execute(
                select(VOCEvidence)
                .where(VOCEvidence.audience_profile_id == audience_profile_id)
                .order_by(VOCEvidence.created_at.asc(), VOCEvidence.id.asc())
            )
            .scalars()
            .all()
        )
