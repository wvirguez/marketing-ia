"""Content API surface (BACKEND-10 §28, lifecycle + approval writes added
by MVP-17B) — exactly:

    GET  /api/v1/campaigns/{campaign_id}/content
    GET  /api/v1/campaigns/{campaign_id}/content/{content_id}
    POST /api/v1/campaigns/{campaign_id}/content/{content_id}/mark-in-production
    POST /api/v1/campaigns/{campaign_id}/content/{content_id}/mark-produced
    POST /api/v1/campaigns/{campaign_id}/content/{content_id}/mark-ready-for-review
    POST /api/v1/campaigns/{campaign_id}/content/{content_id}/versions
    POST /api/v1/campaigns/{campaign_id}/content/{content_id}/request-approval
    POST /api/v1/campaigns/{campaign_id}/content/{content_id}/approvals/{approval_id}/mark-under-review
    POST /api/v1/campaigns/{campaign_id}/content/{content_id}/approvals/{approval_id}/decision
    POST /api/v1/campaigns/{campaign_id}/plan/items/{plan_item_id}/brief   (MVP-34A/-34B)
    POST /api/v1/campaigns/{campaign_id}/content/briefs/{content_brief_id}/pieces   (MVP-35A/-35B)

MVP-18B adds only two human-recorded Distribution transitions. MVP-20 adds
only the one narrow ``.../versions`` revision route described above. MVP-34B
adds the one narrow governed Content Brief create route (frozen MVP-34A
contract) — free text only, immutable/create-only (409 on a second attempt
for the same PlanItem via the pre-existing ``uq_content_briefs_plan_item_id``
constraint), no archive or external publishing route exists. MVP-35B adds
the one narrow governed Content Piece create route (frozen MVP-35A
contract) — ``ContentBrief`` is the sole semantic parent, cardinality is
deliberately ``ContentBrief 1 -> 0..N ContentPiece`` (no uniqueness on
``content_brief_id`` exists or is added; repeated creation against the same
Brief is legitimate, never a 409), and it atomically creates the mandatory
initial ``ContentVersion`` alongside the Piece (MVP-35A §O: a bare Piece
has no legal HTTP route to ever acquire its first Version — see
``.../versions`` below). ``.../versions`` is legal only while the Piece is
REVISION_REQUESTED (see ``ContentService.create_revision_version``) — it is
not a generic Version CRUD surface: no GET/PUT/PATCH/DELETE exists for it.
Neither the Content Brief nor the Content Piece create route has any
GET/PUT/PATCH/DELETE of its own — see ``app/planning/router.py``'s own
``GET /plan``, which embeds each current PlanItem's Brief (MVP-34A §Q), and
this module's own ``GET /content``/``GET /content/{id}`` below, which
already lists every Piece regardless of how it was created (MVP-35A §AA).

PERSISTING AN APPROVAL DECISION != HAVING AUTHORITY TO MAKE THAT DECISION.
The ``decision`` route's OWNER/ADMIN gate is an APPLICATION authorization
choice for the current human-in-the-loop MVP (MVP-17A-R1 §B/§G,
evidenced by `docs/backend/BACKEND-01-API-MAP.md`'s own delegation text)
— it does not claim AGENT-00 itself executed anything, and nothing here
calls ``require_role`` as a substitute for content governance authority on
any of the other five routes, which remain open to any active workspace
member.

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching the same bounded-context separation already applied to
``app/strategy/router.py``/``app/planning/router.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf, require_role
from app.campaigns.service import CampaignAccessService
from app.content.models import ContentPiece
from app.content.repository import ContentBriefRepository
from app.content.schemas import (
    ContentBriefPublic,
    ContentPieceDetailResponse,
    ContentPieceListResponse,
    CreateContentBriefRequest,
    CreateContentPieceRequest,
    CreateContentVersionRequest,
    DistributionPublic,
    RecordApprovalDecisionRequest,
    RecordDistributedRequest,
    TrackingRequirementAssociationRequest,
    content_approval_to_public,
    content_brief_to_public,
    content_piece_to_public,
    content_version_to_public,
    distribution_to_public,
)
from app.content.service import ContentService
from app.core.api_errors import ForbiddenError
from app.persistence.session import get_db
from app.planning.repository import ContentPlanRepository, PlanItemRepository
from app.tracking.repository import TrackingRequirementRepository
from app.users.models import User
from app.workspaces.models import MembershipRole, Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}", tags=["content"])


def _get_authorized_piece(
    db: Session, *, workspace: Workspace, campaign_public_id: str, content_public_id: str,
    piece_only: bool = False,
) -> ContentPiece:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = ContentService(db)
    if piece_only:
        piece = service.get_piece_for_campaign(campaign_id=campaign.id, content_piece_public_id=content_public_id)
    else:
        piece_result = service.get_piece_detail_for_campaign(
            campaign_id=campaign.id, content_piece_public_id=content_public_id
        )
        piece = piece_result[0] if piece_result is not None else None
    if piece is None:
        # Non-leaky: a Content Piece that does not exist, or that exists
        # but belongs to a different campaign, is indistinguishable
        # (mirrors CampaignAccessService's own precedent).
        raise ForbiddenError()
    return piece


def _authorize_approval_belongs_to_piece(content_service: ContentService, *, approval_public_id: str, piece: ContentPiece) -> None:
    """MVP-17A §AD / MVP-17B §14: the underlying service methods only
    re-verify workspace-level tenancy for an Approval, not that it
    belongs to the specific Content Piece named in this URL. An Approval
    that does not exist, or that exists but belongs to a *different*
    Content Piece in the same workspace, is indistinguishable — both are
    rejected the same non-leaky way, closing the same class of
    URL-scoping gap already closed for Assets."""
    approval = content_service.approvals.get_by_public_id(approval_public_id)
    if approval is None:
        raise ForbiddenError()
    version = content_service.versions.get_by_id(approval.content_version_id)
    if version is None or version.content_piece_id != piece.id:
        raise ForbiddenError()


def _distribution_public(content_service: ContentService, distribution) -> DistributionPublic:
    """MVP-24: cross-references TrackingRequirement public IDs only — a
    batched lookup, never a copied Requirement name/status (MVP-24A-R1
    §H: identity-only)."""
    requirement_ids = content_service.list_tracking_requirement_ids_for_distribution(distribution.id)
    requirements = TrackingRequirementRepository(content_service.session).list_for_ids(requirement_ids)
    return distribution_to_public(
        distribution, tracking_requirement_ids=[r.public_id for r in requirements]
    )


# --- Content Brief: governed creation (MVP-34A/-34B) -----------------------
# Frozen contract (MVP-34A): PlanItem is the sole route-named parent —
# content_plan_id/workspace_id are never client-supplied, both are derived
# server-side, closing the "PlanItem belongs to a different Plan" case by
# construction rather than by a runtime check. Historical (non-current)
# ContentPlan PlanItems remain eligible (MVP-34A §G) — the lookup below is
# deliberately unfiltered by ContentPlan currency.


@router.post(
    "/plan/items/{plan_item_public_id}/brief",
    response_model=ContentBriefPublic,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def create_content_brief(
    campaign_public_id: str,
    plan_item_public_id: str,
    payload: CreateContentBriefRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentBriefPublic:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    plan_item = PlanItemRepository(db).get_for_campaign_by_public_id(
        campaign_id=campaign.id, public_id=plan_item_public_id
    )
    if plan_item is None:
        # Non-leaky: a PlanItem that does not exist, or that exists but
        # belongs to a different Campaign, is indistinguishable.
        raise ForbiddenError()
    content_plan = ContentPlanRepository(db).get_by_id(plan_item.content_plan_id)
    if content_plan is None:
        raise ForbiddenError()

    content_service = ContentService(db)
    brief = content_service.record_brief(
        plan_item=plan_item,
        content_plan=content_plan,
        brief=payload.brief,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return content_brief_to_public(
        brief, plan_item_public_id=plan_item.public_id, content_plan_public_id=content_plan.public_id
    )


# --- Content Piece: governed creation (MVP-35A/-35B) ------------------------
# Frozen contract (MVP-35A): ContentBrief is the sole semantic parent —
# content_plan_id/plan_item_id/workspace_id/experiment_id are never
# client-supplied, all derived server-side. Deliberately
# ContentBrief 1 -> 0..N ContentPiece — no uniqueness on content_brief_id
# exists or is added (MVP-35A §D); repeated creation against the same
# Brief is legitimate and never rejected as a duplicate (MVP-35A §E).
# Historical (non-current) ContentPlan Briefs remain eligible (MVP-35A
# §K) — the lookup below is deliberately unfiltered by ContentPlan
# currency. Atomically creates the mandatory initial ContentVersion
# alongside the Piece (MVP-35A §O) by reusing ContentService.record_piece
# unchanged — no write logic is duplicated here.


@router.post(
    "/content/briefs/{content_brief_public_id}/pieces",
    response_model=ContentPieceDetailResponse,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def create_content_piece(
    campaign_public_id: str,
    content_brief_public_id: str,
    payload: CreateContentPieceRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    content_brief = ContentBriefRepository(db).get_for_campaign_by_public_id(
        campaign_id=campaign.id, public_id=content_brief_public_id
    )
    if content_brief is None:
        # Non-leaky: a ContentBrief that does not exist, or that exists but
        # belongs to a different Campaign, is indistinguishable.
        raise ForbiddenError()

    content_service = ContentService(db)
    piece, _version = content_service.record_piece(
        content_brief=content_brief,
        format=payload.format,
        objective=payload.objective,
        funnel_stage=payload.funnel_stage,
        cta=payload.cta,
        channel=payload.channel,
        initial_payload=payload.payload,
        created_by_user_id=user.id,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return _build_detail_response(content_service, piece=piece)


def _build_detail_response(content_service: ContentService, *, piece: ContentPiece) -> ContentPieceDetailResponse:
    """AUTHORITATIVE SERVER TRUTH (MVP-17B §32): always reloads the
    latest Version/Approval fresh from the database rather than trusting
    any caller-held state — every mutation route returns this same
    reload, never a synthesized/optimistic object."""
    latest_version = content_service.get_latest_version_for_piece(piece.id)
    latest_approval = content_service.approvals.get_latest_for_version(latest_version.id) if latest_version is not None else None
    distribution = content_service.get_distribution_for_piece(piece.id)
    return ContentPieceDetailResponse(
        piece=content_piece_to_public(piece),
        latest_version=content_version_to_public(latest_version) if latest_version is not None else None,
        latest_approval=content_approval_to_public(latest_approval) if latest_approval is not None else None,
        distribution=_distribution_public(content_service, distribution) if distribution is not None else None,
    )


@router.post(
    "/content/{content_public_id}/distribution/mark-ready-for-distribution",
    response_model=ContentPieceDetailResponse, status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def mark_ready_for_distribution(
    campaign_public_id: str, content_public_id: str,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    piece = _get_authorized_piece(db, workspace=workspace, campaign_public_id=campaign_public_id, content_public_id=content_public_id, piece_only=True)
    service = ContentService(db)
    service.mark_ready_for_distribution(workspace_id=workspace.id, content_piece_public_id=piece.public_id, actor_user_id=user.id)
    return _build_detail_response(service, piece=piece)


@router.post(
    "/content/{content_public_id}/distribution/record-distributed",
    response_model=ContentPieceDetailResponse,
    dependencies=[Depends(require_csrf), Depends(require_role(MembershipRole.OWNER, MembershipRole.ADMIN))],
)
async def record_distributed(
    campaign_public_id: str, content_public_id: str, payload: RecordDistributedRequest | None = None,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    piece = _get_authorized_piece(db, workspace=workspace, campaign_public_id=campaign_public_id, content_public_id=content_public_id, piece_only=True)
    service = ContentService(db)
    service.record_distributed(
        workspace_id=workspace.id, content_piece_public_id=piece.public_id,
        actor_user_id=user.id, external_reference=payload.external_reference if payload is not None else None,
    )
    return _build_detail_response(service, piece=piece)


# --- MVP-24: ContentDistribution <-> TrackingRequirement association ------
# IDENTITY-ONLY (MVP-24A-R1): the request/response carry pair identity
# only — never TrackingRequirement.status/TrackingPlan.status. Any active
# workspace member may associate/dissociate (matches Tracking's own CRUD
# authority level, MVP-24A §Y) — never require_role, this is workflow
# progression, not a governed decision.


@router.post(
    "/content/{content_public_id}/distribution/tracking-requirements",
    response_model=ContentPieceDetailResponse, status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def associate_tracking_requirement(
    campaign_public_id: str, content_public_id: str, payload: TrackingRequirementAssociationRequest,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = ContentService(db)
    piece = service.get_piece_for_campaign(campaign_id=campaign.id, content_piece_public_id=content_public_id)
    if piece is None:
        raise ForbiddenError()
    requirement = TrackingRequirementRepository(db).get_for_campaign_by_public_id(
        campaign_id=campaign.id, public_id=payload.tracking_requirement_id
    )
    if requirement is None:
        raise ForbiddenError()
    service.associate_tracking_requirement(
        workspace_id=workspace.id, content_piece_public_id=piece.public_id,
        tracking_requirement=requirement, actor_user_id=user.id,
    )
    return _build_detail_response(service, piece=piece)


@router.post(
    "/content/{content_public_id}/distribution/tracking-requirements/{tracking_requirement_public_id}/remove",
    response_model=ContentPieceDetailResponse,
    dependencies=[Depends(require_csrf)],
)
async def dissociate_tracking_requirement(
    campaign_public_id: str, content_public_id: str, tracking_requirement_public_id: str,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = ContentService(db)
    piece = service.get_piece_for_campaign(campaign_id=campaign.id, content_piece_public_id=content_public_id)
    if piece is None:
        raise ForbiddenError()
    requirement = TrackingRequirementRepository(db).get_for_campaign_by_public_id(
        campaign_id=campaign.id, public_id=tracking_requirement_public_id
    )
    if requirement is None:
        raise ForbiddenError()
    service.dissociate_tracking_requirement(
        workspace_id=workspace.id, content_piece_public_id=piece.public_id,
        tracking_requirement=requirement, actor_user_id=user.id,
    )
    return _build_detail_response(service, piece=piece)


@router.get("/content", response_model=ContentPieceListResponse)
async def list_content(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceListResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    pieces = ContentService(db).list_pieces_for_campaign(campaign_id=campaign.id)
    return ContentPieceListResponse(items=[content_piece_to_public(p) for p in pieces])


@router.get("/content/{content_public_id}", response_model=ContentPieceDetailResponse)
async def get_content_detail(
    campaign_public_id: str,
    content_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    piece = _get_authorized_piece(
        db, workspace=workspace, campaign_public_id=campaign_public_id, content_public_id=content_public_id
    )
    return _build_detail_response(ContentService(db), piece=piece)


# --- lifecycle transitions (MVP-17B §15) -----------------------------------


@router.post(
    "/content/{content_public_id}/mark-in-production",
    response_model=ContentPieceDetailResponse,
    dependencies=[Depends(require_csrf)],
)
async def mark_content_in_production(
    campaign_public_id: str,
    content_public_id: str,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    piece = _get_authorized_piece(
        db, workspace=workspace, campaign_public_id=campaign_public_id, content_public_id=content_public_id
    )
    content_service = ContentService(db)
    content_service.mark_in_production(workspace_id=workspace.id, content_piece_public_id=piece.public_id, actor_user_id=user.id)
    return _build_detail_response(content_service, piece=piece)


@router.post(
    "/content/{content_public_id}/mark-produced",
    response_model=ContentPieceDetailResponse,
    dependencies=[Depends(require_csrf)],
)
async def mark_content_produced(
    campaign_public_id: str,
    content_public_id: str,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    piece = _get_authorized_piece(
        db, workspace=workspace, campaign_public_id=campaign_public_id, content_public_id=content_public_id
    )
    content_service = ContentService(db)
    content_service.mark_produced(workspace_id=workspace.id, content_piece_public_id=piece.public_id, actor_user_id=user.id)
    return _build_detail_response(content_service, piece=piece)


@router.post(
    "/content/{content_public_id}/mark-ready-for-review",
    response_model=ContentPieceDetailResponse,
    dependencies=[Depends(require_csrf)],
)
async def mark_content_ready_for_review(
    campaign_public_id: str,
    content_public_id: str,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    piece = _get_authorized_piece(
        db, workspace=workspace, campaign_public_id=campaign_public_id, content_public_id=content_public_id
    )
    content_service = ContentService(db)
    content_service.mark_ready_for_review(workspace_id=workspace.id, content_piece_public_id=piece.public_id, actor_user_id=user.id)
    return _build_detail_response(content_service, piece=piece)


# --- revision loop (MVP-20) -------------------------------------------------


@router.post(
    "/content/{content_public_id}/versions",
    response_model=ContentPieceDetailResponse,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def create_content_revision_version(
    campaign_public_id: str,
    content_public_id: str,
    payload: CreateContentVersionRequest,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    """MVP-20 / CONTENT-P0-6: the only route authorized to move a Piece
    from REVISION_REQUESTED to IN_PRODUCTION — it atomically creates the
    new immutable ContentVersion required for that transition
    (``ContentService.create_revision_version``). Open to any active
    workspace member, matching every other production-bookkeeping route
    (this is not a governance/approval decision)."""
    piece = _get_authorized_piece(
        db, workspace=workspace, campaign_public_id=campaign_public_id, content_public_id=content_public_id,
        piece_only=True,
    )
    content_service = ContentService(db)
    content_service.create_revision_version(
        workspace_id=workspace.id, content_piece_public_id=piece.public_id,
        payload=payload.payload, created_by_user_id=user.id, actor_user_id=user.id,
    )
    return _build_detail_response(content_service, piece=piece)


# --- approval workflow (MVP-17B §16-§28) ------------------------------------


@router.post(
    "/content/{content_public_id}/request-approval",
    response_model=ContentPieceDetailResponse,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def request_content_approval(
    campaign_public_id: str,
    content_public_id: str,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    """Repaired contract (MVP-20A-R1, CONTENT-P3-4/CONTENT-P3-5): the
    router only resolves and authorizes the Piece by URL scope — it no
    longer pre-resolves ``latest_version`` nor pre-checks
    READY_FOR_REVIEW itself. ``ContentService.request_approval`` is the
    single, authoritative owner of the Piece lock, the post-lock status
    re-check, and the post-lock current-Version resolution (see its own
    docstring)."""
    piece = _get_authorized_piece(
        db, workspace=workspace, campaign_public_id=campaign_public_id, content_public_id=content_public_id,
        piece_only=True,
    )
    content_service = ContentService(db)
    content_service.request_approval(
        workspace_id=workspace.id, content_piece_public_id=piece.public_id, actor_user_id=user.id
    )
    return _build_detail_response(content_service, piece=piece)


@router.post(
    "/content/{content_public_id}/approvals/{approval_public_id}/mark-under-review",
    response_model=ContentPieceDetailResponse,
    dependencies=[Depends(require_csrf)],
)
async def mark_content_approval_under_review(
    campaign_public_id: str,
    content_public_id: str,
    approval_public_id: str,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    piece = _get_authorized_piece(
        db, workspace=workspace, campaign_public_id=campaign_public_id, content_public_id=content_public_id
    )
    content_service = ContentService(db)
    _authorize_approval_belongs_to_piece(content_service, approval_public_id=approval_public_id, piece=piece)
    content_service.mark_under_review(
        workspace_id=workspace.id, content_approval_public_id=approval_public_id, actor_user_id=user.id
    )
    return _build_detail_response(content_service, piece=piece)


@router.post(
    "/content/{content_public_id}/approvals/{approval_public_id}/decision",
    response_model=ContentPieceDetailResponse,
    dependencies=[Depends(require_csrf), Depends(require_role(MembershipRole.OWNER, MembershipRole.ADMIN))],
)
async def record_content_approval_decision(
    campaign_public_id: str,
    content_public_id: str,
    approval_public_id: str,
    payload: RecordApprovalDecisionRequest,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ContentPieceDetailResponse:
    """FINAL CONTENT APPROVAL GOVERNANCE AUTHORITY = AGENT-00 (conceptual);
    AGENT-00 AUTHORITY TECHNICALLY REPRESENTED = PARTIAL (MVP-17A-R1 §AB).
    ``require_role`` only gates *application* authorization to record this
    decision — it never claims to be, or to verify, AGENT-00's governance
    authority itself (``app/content/service.py``'s own module docstring)."""
    piece = _get_authorized_piece(
        db, workspace=workspace, campaign_public_id=campaign_public_id, content_public_id=content_public_id
    )
    content_service = ContentService(db)
    _authorize_approval_belongs_to_piece(content_service, approval_public_id=approval_public_id, piece=piece)
    content_service.record_authorized_approval_decision(
        workspace_id=workspace.id,
        content_approval_public_id=approval_public_id,
        decision=payload.decision,
        actor_user_id=user.id,
    )
    return _build_detail_response(content_service, piece=piece)
