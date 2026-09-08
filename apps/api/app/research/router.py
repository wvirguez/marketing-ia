"""Research/Audience API surface (BACKEND-07 §15) — the exact,
canonical, GET-only routes `docs/backend/BACKEND-01-API-MAP.md` §2
defines:

    GET /api/v1/campaigns/{campaign_id}/research
    GET /api/v1/campaigns/{campaign_id}/audience

No write endpoint exists here at all — BACKEND-01's own API map marks
both routes GET-only, and nothing in this stage has a legitimate trigger
to create research/audience output yet (no Agent Run/Gate Decision
exists). Writes are service-layer only (``app/research/service.py``).

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching BACKEND-01's bounded-context separation of `research`
from `campaigns` (§9) — the same reasoning already applied to
``app/orchestration/router.py``, whose paths coexist under the same
``/campaigns/...`` URL namespace without conflict.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_workspace
from app.campaigns.service import CampaignAccessService
from app.persistence.session import get_db
from app.research.schemas import (
    AudienceOutputResponse,
    ResearchOutputResponse,
    audience_profile_to_public,
    research_report_to_public,
    research_source_to_public,
    voc_evidence_to_public,
)
from app.research.service import ResearchService
from app.workspaces.models import Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}", tags=["research"])


@router.get("/research", response_model=ResearchOutputResponse)
async def get_research(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ResearchOutputResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    report, sources = ResearchService(db).get_research_output(campaign=campaign)
    return ResearchOutputResponse(
        report=research_report_to_public(report, campaign_public_id=campaign.public_id) if report else None,
        sources=[research_source_to_public(s) for s in sources],
    )


@router.get("/audience", response_model=AudienceOutputResponse)
async def get_audience(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> AudienceOutputResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    profile, voc_evidence = ResearchService(db).get_audience_output(campaign=campaign)
    return AudienceOutputResponse(
        profile=audience_profile_to_public(profile, campaign_public_id=campaign.public_id) if profile else None,
        voc_evidence=[voc_evidence_to_public(v) for v in voc_evidence],
    )
