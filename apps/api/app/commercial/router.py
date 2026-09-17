"""Commercial API surface (MVP-27, frozen by MVP-27A/-R1/-R2) — exactly:

    GET  /api/v1/campaigns/{campaign_id}/commercial-objectives
    POST /api/v1/campaigns/{campaign_id}/commercial-objectives
    POST /api/v1/campaigns/{campaign_id}/commercial-objectives/{objective_id}/supersede
    GET  /api/v1/campaigns/{campaign_id}/offers
    POST /api/v1/campaigns/{campaign_id}/offers
    POST /api/v1/campaigns/{campaign_id}/offers/{offer_id}/supersede

POST (plain) always creates one additional, independent, current row —
never replaces anything, never designates itself primary. POST
``.../supersede`` is a distinct, atomic, one-shot domain action against
one specific existing row — the two are never conflated (MVP-27A-R1 §N).

Authority: any active membership may create or supersede (MVP-27A-R1
§14/§15/§16 — proposing/updating a commercial objective or offer is not
a governed decision requiring OWNER/ADMIN, the same authority level
Learning's own Implication/Recommendation creation already uses). No
``ActorType.AGENT``, no service-account/system production path — every
route requires ``get_current_user`` (an authenticated human).

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching the same bounded-context separation already applied to
``app/learning/router.py``/``app/tracking/router.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf
from app.campaigns.service import CampaignAccessService
from app.commercial.models import CommercialObjective, Offer
from app.commercial.schemas import (
    CommercialObjectivePublic,
    CreateCommercialObjectiveRequest,
    CreateOfferRequest,
    OfferPublic,
    commercial_objective_to_public,
    offer_to_public,
)
from app.commercial.service import CommercialService
from app.persistence.session import get_db
from app.users.models import User
from app.workspaces.models import Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}", tags=["commercial"])


def _objectives_to_public(
    objectives: list[CommercialObjective], *, campaign_public_id: str
) -> list[CommercialObjectivePublic]:
    """Batched: resolves every supersession successor's public_id from an
    in-memory map built off the same already-fetched list — never one
    query per row (mirrors ``app/learning/router.py``'s own N+1-avoiding
    cross-reference pattern)."""
    public_id_by_id = {o.id: o.public_id for o in objectives}
    return [
        commercial_objective_to_public(
            objective,
            campaign_public_id=campaign_public_id,
            superseded_by_public_id=public_id_by_id.get(objective.superseded_by_commercial_objective_id),
        )
        for objective in objectives
    ]


def _offers_to_public(offers: list[Offer], *, campaign_public_id: str) -> list[OfferPublic]:
    public_id_by_id = {o.id: o.public_id for o in offers}
    return [
        offer_to_public(
            offer,
            campaign_public_id=campaign_public_id,
            superseded_by_public_id=public_id_by_id.get(offer.superseded_by_offer_id),
        )
        for offer in offers
    ]


@router.get("/commercial-objectives", response_model=list[CommercialObjectivePublic])
async def list_commercial_objectives(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[CommercialObjectivePublic]:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    objectives = CommercialService(db).list_objectives_for_campaign(campaign.id)
    return _objectives_to_public(objectives, campaign_public_id=campaign.public_id)


@router.post(
    "/commercial-objectives",
    response_model=CommercialObjectivePublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_commercial_objective(
    campaign_public_id: str,
    payload: CreateCommercialObjectiveRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CommercialObjectivePublic:
    """Creates one additional, independent, current CommercialObjective.
    Cardinality is deliberately 0..N — no uniqueness constraint, two
    calls create two rows, neither replaces the other."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    objective = CommercialService(db).record_commercial_objective(
        campaign=campaign, statement=payload.statement, actor_user_id=user.id, request_id=request.state.request_id
    )
    return commercial_objective_to_public(objective, campaign_public_id=campaign.public_id)


@router.post(
    "/commercial-objectives/{objective_public_id}/supersede",
    response_model=CommercialObjectivePublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def supersede_commercial_objective(
    campaign_public_id: str,
    objective_public_id: str,
    payload: CreateCommercialObjectiveRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CommercialObjectivePublic:
    """Atomic supersession: locks the specific original Objective row,
    creates a fresh replacement, marks the original historical. A second
    attempt against an already-superseded original deterministically
    409s (``CommercialObjectiveAlreadySupersededError``) — never a silent
    second replacement."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    replacement = CommercialService(db).supersede_commercial_objective(
        campaign=campaign,
        objective_public_id=objective_public_id,
        statement=payload.statement,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return commercial_objective_to_public(replacement, campaign_public_id=campaign.public_id)


@router.get("/offers", response_model=list[OfferPublic])
async def list_offers(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[OfferPublic]:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    offers = CommercialService(db).list_offers_for_campaign(campaign.id)
    return _offers_to_public(offers, campaign_public_id=campaign.public_id)


@router.post(
    "/offers", response_model=OfferPublic, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_csrf)]
)
async def create_offer(
    campaign_public_id: str,
    payload: CreateOfferRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> OfferPublic:
    """Creates one additional, independent, current Offer. Cardinality is
    deliberately 0..N — multiple current Offers are valid."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    offer = CommercialService(db).record_offer(
        campaign=campaign,
        statement=payload.statement,
        price=payload.price,
        currency=payload.currency,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return offer_to_public(offer, campaign_public_id=campaign.public_id)


@router.post(
    "/offers/{offer_public_id}/supersede",
    response_model=OfferPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def supersede_offer(
    campaign_public_id: str,
    offer_public_id: str,
    payload: CreateOfferRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> OfferPublic:
    """Atomic supersession — identical shape to
    ``supersede_commercial_objective`` above. A second attempt against an
    already-superseded original deterministically 409s
    (``OfferAlreadySupersededError``)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    replacement = CommercialService(db).supersede_offer(
        campaign=campaign,
        offer_public_id=offer_public_id,
        statement=payload.statement,
        price=payload.price,
        currency=payload.currency,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return offer_to_public(replacement, campaign_public_id=campaign.public_id)
