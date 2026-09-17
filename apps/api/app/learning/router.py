"""Learning API surface (BACKEND-14 Governance Freeze §S/§T, repaired by
Freeze-R GF-D26; extended by MVP-12B-A/-R1/-R2, MVP-23B, MVP-25, MVP-26) —
exactly:

    GET   /api/v1/campaigns/{campaign_id}/learning
    POST  /api/v1/campaigns/{campaign_id}/learning/derive
    POST  /api/v1/campaigns/{campaign_id}/learning/{learning_candidate_id}/mark-provisional
    POST  /api/v1/campaigns/{campaign_id}/learning/{learning_candidate_id}/mark-validation-pending
    POST  /api/v1/campaigns/{campaign_id}/learning/{learning_candidate_id}/decision
    POST  /api/v1/campaigns/{campaign_id}/learning/{learning_candidate_id}/strategic-implications
    POST  /api/v1/campaigns/{campaign_id}/learning/{learning_candidate_id}/recommendations
    PATCH /api/v1/campaigns/{campaign_id}/learning/{recommendation_id}
    PATCH /api/v1/campaigns/{campaign_id}/learning/{learning_candidate_id}/qualification
    POST  /api/v1/campaigns/{campaign_id}/learning/{learning_candidate_id}/qualification/signals
    POST  /api/v1/campaigns/{campaign_id}/learning/{learning_candidate_id}/qualification/signals/{signal_id}/dispose

MVP-26/MVP-26A-R1: ``strategic-implications`` creates a bounded, immutable,
human-authored interpretation of a VALIDATED + sufficiently-qualified
LearningCandidate — never itself a Recommendation, Decision, or Approval.
``recommendations`` now REQUIRES ``strategic_implication_id`` for every
new write (no bypass), resolved server-side under the same Campaign scope
as the target LearningCandidate; existing rows created before this
requirement keep ``strategic_implication_id = NULL`` unchanged, remain
readable and decidable, and are never backfilled.

POST /derive is the explicit, deterministic Measurement -> Learning bridge
(MVP-12B): it takes no request body, derives (or safely reuses) a
LearningCandidate for every AnalysisResult already persisted for this
campaign, and returns the same LearningResponse shape as GET — it never
creates a StrategicRecommendationCandidate and never promotes a
candidate's status past CANDIDATE_IDENTIFIED.

MVP-23A/MVP-23A-R1/MVP-23B: the four new POST routes are the sole product
path past CANDIDATE_IDENTIFIED. Every one reuses the single generic
``LearningService.transition_learning_candidate``/
``record_strategic_recommendation_candidate`` — no per-edge service method
is added merely to mirror a route (MVP-23B §10). ``mark-validation-pending``
deliberately serves both ``PROVISIONAL -> VALIDATION_PENDING`` and the
``INSUFFICIENT_EVIDENCE -> VALIDATION_PENDING`` reopen edge (MVP-23A-R1) —
the centralized transition graph, not the route, is authoritative for which
source states are legal, and the audit event's own ``previous_state``
disambiguates the two afterward.

``decision`` (the final governed Learning judgment) and the existing
recommendation-decision PATCH route both require ``OWNER``/``ADMIN``
(``require_role``) — ``mark-provisional``/``mark-validation-pending``/
``recommendations`` require only active membership (MVP-23A §U/§AC,
MVP-23A-R1 §I). Closes LEARNING-P3-1: the PATCH route previously had no
role dependency at all.

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching the same bounded-context separation already applied to
``app/measurement/router.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf, require_role
from app.campaigns.models import Campaign
from app.campaigns.service import CampaignAccessService
from app.core.api_errors import ForbiddenError
from app.learning.models import LearningCandidate, LearningCandidateStatus, StrategicImplication
from app.learning.schemas import (
    CreateRecommendationRequest,
    CreateStrategicImplicationRequest,
    LearningCandidateDecisionRequest,
    LearningCandidatePublic,
    LearningResponse,
    RecommendationDecisionRequest,
    StrategicImplicationPublic,
    StrategicRecommendationCandidatePublic,
    learning_candidate_to_public,
    recommendation_to_public,
    strategic_implication_to_public,
)
from app.learning.service import LearningService
from app.learning.qualification import QualificationService
from app.learning.qualification_schemas import QualificationPatch, QualificationAttach, QualificationDispose
from app.measurement.models import AnalysisResult
from app.measurement.repository import AnalysisResultRepository
from app.persistence.session import get_db
from app.users.models import User
from app.workspaces.models import MembershipRole, Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}/learning", tags=["learning"])


def _authorized_candidate(
    service: LearningService, *, campaign: Campaign, learning_candidate_public_id: str, for_update: bool = False
) -> LearningCandidate:
    """Campaign-scoped, non-leaky resource resolution (MVP-23B §7/§20): a
    candidate that does not exist at all, that belongs to a different
    workspace, or that belongs to a different Campaign within the *same*
    workspace, is indistinguishable — all three raise the same
    ``ForbiddenError``, mirroring ``decide_recommendation``'s own existing
    convention below."""
    candidate = service.candidates.get_for_campaign_by_public_id(
        campaign_id=campaign.id, public_id=learning_candidate_public_id, for_update=for_update
    )
    if candidate is None:
        raise ForbiddenError()
    return candidate


def _candidate_to_public(db: Session, candidate: LearningCandidate) -> LearningCandidatePublic:
    analysis_result = db.get(AnalysisResult, candidate.analysis_result_id)
    if analysis_result is None:  # pragma: no cover - would mean an orphaned FK, never expected
        raise ForbiddenError()
    service = LearningService(db)
    implications = [
        strategic_implication_to_public(implication, learning_candidate_public_id=candidate.public_id)
        for implication in service.implications.list_for_candidate(candidate.id)
    ]
    return learning_candidate_to_public(
        candidate,
        analysis_result_public_id=analysis_result.public_id,
        qualification=QualificationService(db).public(candidate),
        strategic_implications=implications,
    )


def _learning_response_for_campaign(service: LearningService, db: Session, campaign: Campaign) -> LearningResponse:
    candidates = service.list_candidates_for_campaign(campaign_id=campaign.id)
    recommendations = service.list_recommendations_for_campaign(campaign_id=campaign.id)
    implications = service.list_implications_for_campaign(campaign_id=campaign.id)

    # Cross-reference public IDs only — never an internal UUID (mirrors
    # app/measurement/router.py's own `entries_by_id`/`observations_by_id`
    # cross-reference-map pattern).
    analysis_results = AnalysisResultRepository(db).list_for_campaign(campaign.id)
    analysis_result_public_id_by_id = {a.id: a.public_id for a in analysis_results}
    candidate_public_id_by_id = {c.id: c.public_id for c in candidates}
    implication_public_id_by_id = {i.id: i.public_id for i in implications}

    # Batched: one query already fetched every implication for the whole
    # campaign above (list_implications_for_campaign) — grouped here in
    # memory, never one query per candidate (MVP-26 §26, avoids N+1).
    implications_by_candidate_id: dict = {}
    for implication in implications:
        implications_by_candidate_id.setdefault(implication.learning_candidate_id, []).append(implication)

    return LearningResponse(
        learning_candidates=[
            learning_candidate_to_public(
                candidate, analysis_result_public_id=analysis_result_public_id_by_id[candidate.analysis_result_id],
                qualification=QualificationService(db).public(candidate),
                strategic_implications=[
                    strategic_implication_to_public(implication, learning_candidate_public_id=candidate.public_id)
                    for implication in implications_by_candidate_id.get(candidate.id, [])
                ],
            )
            for candidate in candidates
        ],
        strategic_recommendation_candidates=[
            recommendation_to_public(
                recommendation,
                learning_candidate_public_id=candidate_public_id_by_id[recommendation.learning_candidate_id],
                strategic_implication_public_id=implication_public_id_by_id.get(recommendation.strategic_implication_id),
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


@router.post(
    "/{learning_candidate_public_id}/mark-provisional",
    response_model=LearningCandidatePublic,
    dependencies=[Depends(require_csrf)],
)
async def mark_candidate_provisional(
    campaign_public_id: str,
    learning_candidate_public_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> LearningCandidatePublic:
    """No request body. Legal only from CANDIDATE_IDENTIFIED — the
    centralized transition graph (``app/learning/transitions.py``) is
    authoritative, not this route (MVP-23A §U, MVP-23B §11). Any active
    membership may call this; it is workflow progression, not a governed
    decision."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = LearningService(db)
    candidate = _authorized_candidate(
        service, campaign=campaign, learning_candidate_public_id=learning_candidate_public_id, for_update=True
    )
    candidate = service.transition_learning_candidate(
        learning_candidate=candidate,
        target_status=LearningCandidateStatus.PROVISIONAL,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return _candidate_to_public(db, candidate)


@router.post(
    "/{learning_candidate_public_id}/mark-validation-pending",
    response_model=LearningCandidatePublic,
    dependencies=[Depends(require_csrf)],
)
async def mark_candidate_validation_pending(
    campaign_public_id: str,
    learning_candidate_public_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> LearningCandidatePublic:
    """No request body. Deliberately serves BOTH legal edges into
    VALIDATION_PENDING: the first-ever progression from PROVISIONAL, and
    the explicit reopen from INSUFFICIENT_EVIDENCE (MVP-23A-R1) — the
    transition graph decides which source states are legal, not this
    route; a call from any other status deterministically 409s. Reopening
    does not assert new evidence exists (MVP-23A-R1 §E/§F) and does not
    require a higher role than ordinary progression (MVP-23A-R1 §H/§I).
    Any active membership may call this."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = LearningService(db)
    candidate = _authorized_candidate(
        service, campaign=campaign, learning_candidate_public_id=learning_candidate_public_id, for_update=True
    )
    candidate = service.transition_learning_candidate(
        learning_candidate=candidate,
        target_status=LearningCandidateStatus.VALIDATION_PENDING,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return _candidate_to_public(db, candidate)


@router.post(
    "/{learning_candidate_public_id}/decision",
    response_model=LearningCandidatePublic,
    dependencies=[Depends(require_csrf), Depends(require_role(MembershipRole.OWNER, MembershipRole.ADMIN))],
)
async def decide_learning_candidate(
    campaign_public_id: str,
    learning_candidate_public_id: str,
    payload: LearningCandidateDecisionRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> LearningCandidatePublic:
    """The final governed Learning decision — legal only from
    VALIDATION_PENDING, restricted to exactly VALIDATED/REJECTED/
    INSUFFICIENT_EVIDENCE (never an arbitrary status string). OWNER/ADMIN
    only (MVP-23A §Q/§U — this is the structural analog of a Content
    Approval decision, not ordinary workflow progression)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = LearningService(db)
    candidate = _authorized_candidate(
        service, campaign=campaign, learning_candidate_public_id=learning_candidate_public_id, for_update=True
    )
    candidate = service.transition_learning_candidate(
        learning_candidate=candidate,
        target_status=payload.decision,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return _candidate_to_public(db, candidate)


@router.post(
    "/{learning_candidate_public_id}/strategic-implications",
    response_model=StrategicImplicationPublic,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def create_strategic_implication(
    campaign_public_id: str,
    learning_candidate_public_id: str,
    payload: CreateStrategicImplicationRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategicImplicationPublic:
    """Creates a StrategicImplication from a VALIDATED, sufficiently-
    qualified LearningCandidate (MVP-26) — requires only active membership
    (proposing an interpretation is not deciding anything, mirrors
    Recommendation creation's own authority). Cardinality is deliberately
    0..N; no uniqueness constraint — two calls create two rows.
    ``record_strategic_implication`` re-locks and re-reads the candidate
    (canonical lock, MVP-26 §12) and re-checks qualification sufficiency
    as intentional defense-in-depth (MVP-26A-R1 §21)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = LearningService(db)
    candidate = _authorized_candidate(
        service, campaign=campaign, learning_candidate_public_id=learning_candidate_public_id, for_update=False
    )
    implication = service.record_strategic_implication(
        learning_candidate=candidate,
        statement=payload.statement,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return strategic_implication_to_public(implication, learning_candidate_public_id=candidate.public_id)


@router.post(
    "/{learning_candidate_public_id}/recommendations",
    response_model=StrategicRecommendationCandidatePublic,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def create_recommendation(
    campaign_public_id: str,
    learning_candidate_public_id: str,
    payload: CreateRecommendationRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategicRecommendationCandidatePublic:
    """Creates a StrategicRecommendationCandidate from a VALIDATED
    LearningCandidate — requires only active membership (proposing is not
    deciding, MVP-23A §AC). Cardinality is deliberately 0..N; no
    idempotency key, no uniqueness constraint (MVP-23A §AH/§AI) — two
    calls create two rows.

    MVP-26/26A-R1: ``strategic_implication_id`` is resolved server-side,
    under the *same* Campaign scope as ``learning_candidate_public_id``
    (never trusting a client-supplied internal UUID, MVP-26A-R1 §21) —
    nonexistent, wrong-workspace, and wrong-campaign-same-workspace are
    all indistinguishable, the same non-leaky ``ForbiddenError`` every
    other campaign-scoped lookup in this router already uses. A
    same-campaign Implication that belongs to a *different*
    LearningCandidate is a distinct, same-tenant relational conflict,
    raised by the service as ``StrategicImplicationMismatchError``
    (MVP-26A-R1 §H) rather than folded into the same not-found response."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    service = LearningService(db)
    # No lock needed here: recording a recommendation never mutates the
    # parent candidate's row, so a concurrent transition/decision on the
    # same candidate cannot conflict with it (MVP-23A §AK). Both
    # StrategicImplication and LearningCandidate are read, not locked —
    # the Implication is immutable and the candidate is terminal
    # (VALIDATED) by the time any valid Implication for it can exist.
    candidate = _authorized_candidate(
        service, campaign=campaign, learning_candidate_public_id=learning_candidate_public_id, for_update=False
    )
    recommendation = service.record_strategic_recommendation_candidate(
        campaign=campaign,
        learning_candidate=candidate,
        strategic_implication_public_id=payload.strategic_implication_id,
        summary=payload.summary,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return recommendation_to_public(
        recommendation,
        learning_candidate_public_id=candidate.public_id,
        strategic_implication_public_id=payload.strategic_implication_id,
    )


@router.patch(
    "/{recommendation_public_id}",
    response_model=StrategicRecommendationCandidatePublic,
    dependencies=[Depends(require_csrf), Depends(require_role(MembershipRole.OWNER, MembershipRole.ADMIN))],
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
    strategic_implication_public_id = None
    if recommendation.strategic_implication_id is not None:
        implication = db.get(StrategicImplication, recommendation.strategic_implication_id)
        strategic_implication_public_id = implication.public_id if implication else None
    return recommendation_to_public(
        recommendation,
        learning_candidate_public_id=candidate.public_id,
        strategic_implication_public_id=strategic_implication_public_id,
    )


@router.patch("/{learning_candidate_public_id}/qualification", response_model=LearningCandidatePublic, dependencies=[Depends(require_csrf)])
async def update_qualification(campaign_public_id: str, learning_candidate_public_id: str,
    payload: QualificationPatch, request: Request, user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    campaign = CampaignAccessService(db).get_authorized_campaign(workspace_id=workspace.id, campaign_public_id=campaign_public_id)
    candidate = QualificationService(db).mutate(campaign=campaign, candidate_public_id=learning_candidate_public_id,
        actor_user_id=user.id, operation="update", values=payload.model_dump(exclude_unset=True), request_id=request.state.request_id)
    return _candidate_to_public(db, candidate)


@router.post("/{learning_candidate_public_id}/qualification/signals", response_model=LearningCandidatePublic, status_code=201, dependencies=[Depends(require_csrf)])
async def attach_qualification_signal(campaign_public_id: str, learning_candidate_public_id: str,
    payload: QualificationAttach, request: Request, user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    campaign = CampaignAccessService(db).get_authorized_campaign(workspace_id=workspace.id, campaign_public_id=campaign_public_id)
    candidate = QualificationService(db).mutate(campaign=campaign, candidate_public_id=learning_candidate_public_id,
        actor_user_id=user.id, operation="attach", values=payload.model_dump(exclude_unset=True), request_id=request.state.request_id)
    return _candidate_to_public(db, candidate)


@router.post("/{learning_candidate_public_id}/qualification/signals/{signal_public_id}/dispose", response_model=LearningCandidatePublic, dependencies=[Depends(require_csrf)])
async def dispose_qualification_signal(campaign_public_id: str, learning_candidate_public_id: str, signal_public_id: str,
    payload: QualificationDispose, request: Request, user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace), db: Session = Depends(get_db)):
    campaign = CampaignAccessService(db).get_authorized_campaign(workspace_id=workspace.id, campaign_public_id=campaign_public_id)
    candidate = QualificationService(db).mutate(campaign=campaign, candidate_public_id=learning_candidate_public_id,
        actor_user_id=user.id, operation="dispose", values=payload.model_dump(exclude_unset=True), signal_public_id=signal_public_id,
        request_id=request.state.request_id)
    return _candidate_to_public(db, candidate)
