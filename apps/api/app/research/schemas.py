"""Public DTOs for the research/audience read surface. Never expose an
internal UUID or a raw `workspace_id` — only public_id-derived fields
(BACKEND-07 §16)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.research.models import AudienceProfile, ResearchReport, ResearchSource, VOCEvidence


class ResearchSourcePublic(BaseModel):
    id: str
    source_type: str
    title: str
    locator: str | None
    publisher: str | None
    excerpt: str | None
    retrieved_at: datetime | None
    created_at: datetime


class ResearchReportPublic(BaseModel):
    id: str
    campaign_id: str
    version: int
    summary: str
    created_at: datetime


class ResearchOutputResponse(BaseModel):
    report: ResearchReportPublic | None
    sources: list[ResearchSourcePublic]


class VOCEvidencePublic(BaseModel):
    id: str
    verbatim_quote: str | None
    paraphrase: str | None
    source_type: str | None
    source_locator: str | None
    created_at: datetime


class AudienceProfilePublic(BaseModel):
    id: str
    campaign_id: str
    version: int
    summary: str
    created_at: datetime


class AudienceOutputResponse(BaseModel):
    profile: AudienceProfilePublic | None
    voc_evidence: list[VOCEvidencePublic]


def research_source_to_public(source: ResearchSource) -> ResearchSourcePublic:
    return ResearchSourcePublic(
        id=source.public_id,
        source_type=source.source_type.value,
        title=source.title,
        locator=source.locator,
        publisher=source.publisher,
        excerpt=source.excerpt,
        retrieved_at=source.retrieved_at,
        created_at=source.created_at,
    )


def research_report_to_public(report: ResearchReport, *, campaign_public_id: str) -> ResearchReportPublic:
    return ResearchReportPublic(
        id=report.public_id,
        campaign_id=campaign_public_id,
        version=report.version,
        summary=report.summary,
        created_at=report.created_at,
    )


def voc_evidence_to_public(item: VOCEvidence) -> VOCEvidencePublic:
    return VOCEvidencePublic(
        id=item.public_id,
        verbatim_quote=item.verbatim_quote,
        paraphrase=item.paraphrase,
        source_type=item.source_type.value if item.source_type else None,
        source_locator=item.source_locator,
        created_at=item.created_at,
    )


def audience_profile_to_public(profile: AudienceProfile, *, campaign_public_id: str) -> AudienceProfilePublic:
    return AudienceProfilePublic(
        id=profile.public_id,
        campaign_id=campaign_public_id,
        version=profile.version,
        summary=profile.summary,
        created_at=profile.created_at,
    )
