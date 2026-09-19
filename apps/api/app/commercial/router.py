"""Commercial API surface (MVP-27, frozen by MVP-27A/-R1/-R2; extended by
MVP-36, frozen by MVP-36A/-R1) — exactly:

    GET  /api/v1/campaigns/{campaign_id}/commercial-objectives
    POST /api/v1/campaigns/{campaign_id}/commercial-objectives
    POST /api/v1/campaigns/{campaign_id}/commercial-objectives/{objective_id}/supersede
    GET  /api/v1/campaigns/{campaign_id}/offers
    POST /api/v1/campaigns/{campaign_id}/offers
    POST /api/v1/campaigns/{campaign_id}/offers/{offer_id}/supersede
    GET  /api/v1/campaigns/{campaign_id}/commercial-outcomes
    POST /api/v1/campaigns/{campaign_id}/commercial-outcomes
    POST /api/v1/campaigns/{campaign_id}/commercial-outcomes/{outcome_id}/corrections

CommercialOutcome routes are USER-only (MVP-36A §M), MEMBER+ authority
(MVP-36A §N — recording/correcting a reported fact is not a governance
decision, the same reasoning already applied to Objective/Offer above).
Create/correction both return 201 for a genuinely new row and 200 for an
exact idempotent replay (MVP-36A-R1 §4) — never a 409 for a legitimate
retry with the same ``client_request_id`` and matching payload.

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

import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf
from app.campaigns.service import CampaignAccessService
from app.commercial.models import CommercialObjective, CommercialOutcome, Offer
from app.commercial.repository import CommercialOutcomeRepository
from app.commercial.schemas import (
    CommercialObjectivePublic,
    CommercialOutcomePublic,
    CorrectCommercialOutcomeRequest,
    CreateCommercialObjectiveRequest,
    CreateCommercialOutcomeRequest,
    CreateOfferRequest,
    OfferPublic,
    commercial_objective_to_public,
    commercial_outcome_to_public,
    offer_to_public,
)
from app.commercial.service import CommercialService
from app.content.repository import ContentDistributionRepository
from app.core.api_errors import ForbiddenError
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


# --- CommercialOutcome (MVP-36, frozen by MVP-36A/-R1) -----------------------


def _outcome_to_public(db: Session, *, outcome: CommercialOutcome, campaign_public_id: str) -> CommercialOutcomePublic:
    """Single-row conversion (create/correction responses): always
    performs a real ``get_successor`` lookup, since even a just-returned
    idempotent-replay row may already have its own later successor by the
    time of the replay (MVP-36A-R1 §7's own critical-ordering note)."""
    distribution_public_id = None
    if outcome.content_distribution_id is not None:
        distributions = ContentDistributionRepository(db).list_for_ids([outcome.content_distribution_id])
        distribution_public_id = distributions[0].public_id if distributions else None

    outcomes = CommercialOutcomeRepository(db)
    supersedes_public_id = None
    if outcome.supersedes_outcome_id is not None:
        target = outcomes.get_by_id(outcome.supersedes_outcome_id)
        supersedes_public_id = target.public_id if target is not None else None
    successor = outcomes.get_successor(outcome.id)
    corrected_by_public_id = successor.public_id if successor is not None else None

    return commercial_outcome_to_public(
        outcome,
        campaign_public_id=campaign_public_id,
        content_distribution_public_id=distribution_public_id,
        supersedes_public_id=supersedes_public_id,
        corrected_by_public_id=corrected_by_public_id,
    )


def _outcomes_to_public(
    db: Session, outcomes: list[CommercialOutcome], *, campaign_public_id: str
) -> list[CommercialOutcomePublic]:
    """Batched (list route only): resolves every Distribution/forward/
    backward pointer from in-memory maps built off the same already-
    fetched list — never one query per row (mirrors
    ``_objectives_to_public``/``_offers_to_public`` above exactly)."""
    distribution_ids = [o.content_distribution_id for o in outcomes if o.content_distribution_id is not None]
    distributions = ContentDistributionRepository(db).list_for_ids(distribution_ids) if distribution_ids else []
    distribution_public_id_by_id = {d.id: d.public_id for d in distributions}

    public_id_by_id = {o.id: o.public_id for o in outcomes}
    successor_id_by_target_id: dict[uuid.UUID, uuid.UUID] = {
        o.supersedes_outcome_id: o.id for o in outcomes if o.supersedes_outcome_id is not None
    }

    result: list[CommercialOutcomePublic] = []
    for outcome in outcomes:
        distribution_public_id = (
            distribution_public_id_by_id.get(outcome.content_distribution_id)
            if outcome.content_distribution_id is not None
            else None
        )
        supersedes_public_id = (
            public_id_by_id.get(outcome.supersedes_outcome_id) if outcome.supersedes_outcome_id is not None else None
        )
        successor_id = successor_id_by_target_id.get(outcome.id)
        corrected_by_public_id = public_id_by_id.get(successor_id) if successor_id is not None else None
        result.append(
            commercial_outcome_to_public(
                outcome,
                campaign_public_id=campaign_public_id,
                content_distribution_public_id=distribution_public_id,
                supersedes_public_id=supersedes_public_id,
                corrected_by_public_id=corrected_by_public_id,
            )
        )
    return result


def _resolve_content_distribution_id(
    db: Session, *, campaign_id: uuid.UUID, content_distribution_public_id: str | None
) -> uuid.UUID | None:
    """Optional observational provenance only (MVP-36A §G) — resolved
    campaign-scoped, never workspace-scoped alone: a Distribution
    belonging to a different Campaign in the same or a different
    Workspace is rejected identically/non-leakily (MVP-36A §5/§R,
    MVP-36B §20)."""
    if content_distribution_public_id is None:
        return None
    distribution = ContentDistributionRepository(db).get_for_campaign_by_public_id(
        campaign_id=campaign_id, public_id=content_distribution_public_id
    )
    if distribution is None:
        raise ForbiddenError()
    return distribution.id


@router.get("/commercial-outcomes", response_model=list[CommercialOutcomePublic])
async def list_commercial_outcomes(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[CommercialOutcomePublic]:
    """Returns ALL CommercialOutcome rows for the Campaign — current and
    historical alike, each carrying ``is_current``/``supersedes_outcome_id``/
    ``corrected_by_commercial_outcome_id`` (MVP-36A-R1 §12) — never a
    current-only filtered list. Ordered by ``occurred_at`` descending,
    tie-broken by ``created_at`` then ``id`` for deterministic output."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    outcomes = CommercialService(db).list_commercial_outcomes_for_campaign(campaign.id)
    return _outcomes_to_public(db, outcomes, campaign_public_id=campaign.public_id)


@router.post(
    "/commercial-outcomes",
    response_model=CommercialOutcomePublic,
    dependencies=[Depends(require_csrf)],
)
async def create_commercial_outcome(
    campaign_public_id: str,
    payload: CreateCommercialOutcomeRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CommercialOutcomePublic:
    """MVP-36A-R1 §4: 201 for a genuinely new row, 200 for an exact
    idempotent replay — a dynamic status, set on ``response`` directly,
    mirroring ``create_distribution_evidence``'s own precedent exactly.
    409 IDEMPOTENCY_KEY_CONFLICT (the existing, cross-domain
    ``IdempotencyKeyConflictError`` — never a new, redundant error class)
    if ``client_request_id`` was already used for a materially different
    request."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    content_distribution_id = _resolve_content_distribution_id(
        db, campaign_id=campaign.id, content_distribution_public_id=payload.content_distribution_id
    )

    outcome, created = CommercialService(db).record_commercial_outcome(
        campaign=campaign,
        content_distribution_id=content_distribution_id,
        outcome_type=payload.outcome_type,
        quantity=payload.quantity,
        monetary_value=payload.monetary_value,
        currency=payload.currency,
        occurred_at=payload.occurred_at,
        external_reference=payload.external_reference,
        client_request_id=payload.client_request_id,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _outcome_to_public(db, outcome=outcome, campaign_public_id=campaign.public_id)


@router.post(
    "/commercial-outcomes/{outcome_public_id}/corrections",
    response_model=CommercialOutcomePublic,
    dependencies=[Depends(require_csrf)],
)
async def correct_commercial_outcome(
    campaign_public_id: str,
    outcome_public_id: str,
    payload: CorrectCommercialOutcomeRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CommercialOutcomePublic:
    """FULL-STATE correction (MVP-36A-R1 §9) — TIP-ONLY (MVP-36A-R1 §8):
    a 409 COMMERCIAL_OUTCOME_CORRECTION_TARGET_STALE if ``outcome_public_id``
    already has a successor. ``content_distribution_id`` is never accepted
    here — always inherited unconditionally from the target. 201/200 the
    same dynamic-status discipline as create above."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    outcome, created = CommercialService(db).correct_commercial_outcome(
        campaign=campaign,
        target_outcome_public_id=outcome_public_id,
        outcome_type=payload.outcome_type,
        quantity=payload.quantity,
        monetary_value=payload.monetary_value,
        currency=payload.currency,
        occurred_at=payload.occurred_at,
        external_reference=payload.external_reference,
        client_request_id=payload.client_request_id,
        correction_reason=payload.correction_reason,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _outcome_to_public(db, outcome=outcome, campaign_public_id=campaign.public_id)
