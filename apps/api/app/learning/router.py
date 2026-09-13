"""Learning API surface (BACKEND-14 Governance Freeze §S/§T, repaired by
Freeze-R GF-D26; extended by MVP-12B-A/-R1/-R2) — exactly:

    GET   /api/v1/campaigns/{campaign_id}/learning
    PATCH /api/v1/campaigns/{campaign_id}/learning/{recommendation_id}
    POST  /api/v1/campaigns/{campaign_id}/learning/derive

No POST exists that accepts arbitrary caller-supplied LearningCandidate
content, and no PATCH exists for LearningCandidate's own maturity — both
remain service-layer-only (``app/learning/service.py``). PATCH is scoped
strictly to a Strategic Recommendation Candidate's one-shot decision.
POST /derive is the explicit, deterministic Measurement -> Learning bridge
(MVP-12B): it takes no request body, derives (or safely reuses) a
LearningCandidate for every AnalysisResult already persisted for this
campaign, and returns the same LearningResponse shape as GET — it never
creates a StrategicRecommendationCandidate and never promotes a
candidate's status past CANDIDATE_IDENTIFIED.

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching the same bounded-context separation already applied to
``app/measurement/router.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf
from app.campaigns.models import Campaign
from app.campaigns.service import CampaignAccessService
from app.core.api_errors import ForbiddenError
from app.learning.schemas import (
    LearningResponse,
    RecommendationDecisionRequest,
    StrategicRecommendationCandidatePublic,
    learning_candidate_to_public,
    recommendation_to_public,
)
from app.learning.service import LearningService
from app.measurement.repository import AnalysisResultRepository
from app.persistence.session import get_db
from app.users.models import User
from app.workspaces.models import Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}/learning", tags=["learning"])


def _learning_response_for_campaign(service: LearningService, db: Session, campaign: Campaign) -> LearningResponse:
    candidates = service.list_candidates_for_campaign(campaign_id=campaign.id)
    recommendations = service.list_recommendations_for_campaign(campaign_id=campaign.id)

    # Cross-reference public IDs only — never an internal UUID (mirrors
    # app/measurement/router.py's own `entries_by_id`/`observations_by_id`
    # cross-reference-map pattern).
    analysis_results = AnalysisResultRepository(db).list_for_campaign(campaign.id)
    analysis_result_public_id_by_id = {a.id: a.public_id for a in analysis_results}
    candidate_public_id_by_id = {c.id: c.public_id for c in candidates}

    return LearningResponse(
        learning_candidates=[
            learning_candidate_to_public(
                candidate, analysis_result_public_id=analysis_result_public_id_by_id[candidate.analysis_result_id]
            )
            for candidate in candidates
        ],
        strategic_recommendation_candidates=[
            recommendation_to_public(
                recommendation,
                learning_candidate_public_id=candidate_public_id_by_id[recommendation.learning_candidate_id],
            )
            for recommendation in recommendations
        ],
    )


@router.get("", response_model=LearningResponse)
async def list_learning(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> LearningResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = LearningService(db)
    return _learning_response_for_campaign(service, db, campaign)


@router.post("/derive", response_model=LearningResponse, dependencies=[Depends(require_csrf)])
async def derive_learning(
    campaign_public_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> LearningResponse:
    """Explicit, synchronous trigger for the Measurement -> Learning bridge
    (MVP-12B). No request body. A campaign with zero AnalysisResults is a
    successful no-op (HTTP 200, an unchanged/empty LearningResponse), never
    a failure. Every returned entity belongs to this URL's own campaign."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = LearningService(db)
    service.derive_candidates_for_campaign(
        campaign=campaign, actor_user_id=user.id, request_id=request.state.request_id
    )
    return _learning_response_for_campaign(service, db, campaign)


@router.patch(
    "/{recommendation_public_id}",
    response_model=StrategicRecommendationCandidatePublic,
    dependencies=[Depends(require_csrf)],
)
async def decide_recommendation(
    campaign_public_id: str,
    recommendation_public_id: str,
    payload: RecommendationDecisionRequest,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategicRecommendationCandidatePublic:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = LearningService(db)
    recommendation = service.decide_strategic_recommendation_candidate(
        campaign=campaign,
        recommendation_public_id=recommendation_public_id,
        decision=payload.decision,
        actor_user_id=user.id,
    )
    candidate = service.candidates.get_by_id(recommendation.learning_candidate_id)
    if candidate is None:
        # Cannot happen given the tenant-safe composite FK — defensive
        # only, never expected to actually raise.
        raise ForbiddenError()
    return recommendation_to_public(recommendation, learning_candidate_public_id=candidate.public_id)
