"""Request/response DTOs for the campaigns domain.

Never expose an internal UUID primary key or a raw `workspace_id` — only
`public_id`-derived fields (BACKEND-05 §6/§13). Response shapes are
deliberately minimal: no research/strategy/content placeholders, since
none of that exists yet (BACKEND-05 §15/§32).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.campaigns.models import Campaign, CampaignBrief, CampaignRun

_PROMPT_MAX_LENGTH = 4000


class CampaignCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    prompt: str = Field(min_length=1, max_length=_PROMPT_MAX_LENGTH)
    product_type: str | None = Field(default=None, max_length=100)
    price: str | None = Field(default=None, max_length=60)
    audience: str | None = Field(default=None, max_length=200)
    budget: str | None = Field(default=None, max_length=60)
    channel: str | None = Field(default=None, max_length=100)

    @field_validator("name", "prompt")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("This field cannot be blank.")
        return stripped

    @field_validator("product_type", "price", "audience", "budget", "channel")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class CampaignPatchRequest(BaseModel):
    """Only ``name`` is mutable in BACKEND-05 — status transitions belong
    to the orchestration runtime, which does not exist yet (BACKEND-05
    §16)."""

    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("This field cannot be blank.")
        return stripped


class CampaignPublic(BaseModel):
    id: str
    name: str
    status: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CampaignBriefPublic(BaseModel):
    id: str
    campaign_id: str
    version: int
    prompt: str
    product_type: str | None
    price: str | None
    audience: str | None
    budget: str | None
    channel: str | None
    created_at: datetime


class CampaignRunPublic(BaseModel):
    id: str
    campaign_id: str
    run_number: int
    status: str
    created_at: datetime


class CampaignCreateResponse(BaseModel):
    campaign: CampaignPublic
    brief: CampaignBriefPublic
    run: CampaignRunPublic


class CampaignListResponse(BaseModel):
    items: list[CampaignPublic]
    limit: int
    offset: int
    total: int


class CampaignRunListResponse(BaseModel):
    items: list[CampaignRunPublic]
    limit: int
    offset: int
    total: int


def campaign_to_public(campaign: Campaign) -> CampaignPublic:
    return CampaignPublic(
        id=campaign.public_id,
        name=campaign.name,
        status=campaign.status.value,
        archived_at=campaign.archived_at,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


def campaign_brief_to_public(brief: CampaignBrief, *, campaign_public_id: str) -> CampaignBriefPublic:
    return CampaignBriefPublic(
        id=brief.public_id,
        campaign_id=campaign_public_id,
        version=brief.version,
        prompt=brief.prompt,
        product_type=brief.product_type,
        price=brief.price,
        audience=brief.audience,
        budget=brief.budget,
        channel=brief.channel,
        created_at=brief.created_at,
    )


def campaign_run_to_public(run: CampaignRun, *, campaign_public_id: str) -> CampaignRunPublic:
    return CampaignRunPublic(
        id=run.public_id,
        campaign_id=campaign_public_id,
        run_number=run.run_number,
        status=run.status.value,
        created_at=run.created_at,
    )
