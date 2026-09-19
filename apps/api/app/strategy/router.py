"""Strategy API surface (BACKEND-08 §19) — the canonical read route
`docs/backend/BACKEND-01-API-MAP.md` §2 defines:

    GET /api/v1/campaigns/{campaign_id}/strategy

plus four governed write routes and two governed read routes:

    POST /api/v1/campaigns/{campaign_id}/strategy/{strategy_id}/hypotheses
        (MVP-31A/-31A-R1/MVP-31B)
    POST /api/v1/campaigns/{campaign_id}/hypotheses/{hypothesis_id}/experiments
        (MVP-32A/-32A-R1/MVP-32B) — no Strategy in the path: Strategy
        currency is a server-side eligibility check derived exclusively
        from the named Hypothesis's own persisted ``strategy_id`` FK,
        never a client-supplied identifier (MVP-32A-R1 §14).
    POST /api/v1/campaigns/{campaign_id}/experiments/{experiment_id}/definition-versions
    GET  /api/v1/campaigns/{campaign_id}/experiments/{experiment_id}/definition-versions
        (MVP-37, frozen MVP-37A/-37B) — the append-only, versioned Experiment
        Definition: one route declares (``base_version=0``), revises
        (``base_version`` = current tip) or replays; the GET is the version
        history, readable for any Experiment of the campaign including one
        under a superseded Strategy.
    POST /api/v1/campaigns/{campaign_id}/experiments/{experiment_id}/variants
    GET  /api/v1/campaigns/{campaign_id}/experiments/{experiment_id}/variants
        (MVP-38, frozen MVP-38A/-38B) — the immutable identity of one declared
        condition, pinned to the EXPLICITLY named current Definition version
        (declaring the first one derives the Definition lock); the GET is a
        paginated page (limit/offset/total) ordered by pinned version then
        ordinal. Identity only: no allocation, exposure, measurement, role or
        result, and no PATCH/PUT/DELETE.

No other write endpoint exists here — Strategy/Positioning themselves
remain writable only via the deterministic bootstrap
(``app/strategy/service.py::record_strategy``) or the separate governed
Strategy Revision surface (``app/orchestration/strategy_revision_router.py``,
MVP-30B).

Mounted directly on ``api_v1_router`` (not nested inside the campaigns
router), matching the same bounded-context separation already applied to
``app/orchestration/router.py``/``app/research/router.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf
from app.campaigns.service import CampaignAccessService
from app.persistence.session import get_db
from app.strategy.definition_service import ExperimentDefinitionService
from app.strategy.schemas import (
    CreateExperimentRequest,
    CreateHypothesisRequest,
    CreateVariantRequest,
    DeclareExperimentDefinitionRequest,
    ExperimentDefinitionHistoryResponse,
    ExperimentDefinitionPublic,
    ExperimentPublic,
    HypothesisPublic,
    StrategyOutputResponse,
    VariantListResponse,
    VariantPublic,
    comparison_label_for,
    definition_to_public,
    experiment_to_public,
    hypothesis_to_public,
    positioning_to_public,
    strategy_to_public,
    variant_to_public,
)
from app.strategy.service import StrategyService
from app.strategy.variant_service import ExperimentVariantService
from app.users.models import User
from app.workspaces.models import Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}", tags=["strategy"])


@router.get("/strategy", response_model=StrategyOutputResponse)
async def get_strategy(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategyOutputResponse:
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    strategy, positioning, hypotheses, experiments = StrategyService(db).get_strategy_output(campaign=campaign)
    hypothesis_public_id_by_id = {h.id: h.public_id for h in hypotheses}
    definition_service = ExperimentDefinitionService(db)
    definition_tips = definition_service.tips_for_experiments(
        workspace_id=campaign.workspace_id, experiments=experiments
    )
    pin_states = definition_service.pin_states_for_versions(
        workspace_id=campaign.workspace_id, definition_version_ids=[tip.id for tip in definition_tips.values()]
    )
    return StrategyOutputResponse(
        strategy=strategy_to_public(strategy, campaign_public_id=campaign.public_id) if strategy else None,
        positioning=positioning_to_public(positioning) if positioning else None,
        hypotheses=[hypothesis_to_public(h) for h in hypotheses],
        experiments=[
            experiment_to_public(
                e,
                hypothesis_public_id=hypothesis_public_id_by_id[e.hypothesis_id],
                definition=definition_tips.get(e.id),
                definition_pin_state=(
                    pin_states[definition_tips[e.id].id] if e.id in definition_tips else (0, False)
                ),
            )
            for e in experiments
        ],
    )


@router.post(
    "/strategy/{strategy_public_id}/hypotheses",
    response_model=HypothesisPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_hypothesis(
    campaign_public_id: str,
    strategy_public_id: str,
    payload: CreateHypothesisRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> HypothesisPublic:
    """Records a governed next-cycle Hypothesis under the exact named
    Strategy (MVP-31A/-31A-R1, frozen contract). Any active workspace
    membership may call this (MEMBER+, matching
    ``StrategicImplication``/``StrategicRecommendation``'s own precedent —
    a Hypothesis proposes, it never commits or mutates the authoritative
    Strategy/Positioning state). The named Strategy must still be the
    Campaign's own current version (``HypothesisStrategyStaleError``
    otherwise — never silently rebased to whatever is current now)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    hypothesis = StrategyService(db).create_hypothesis(
        campaign=campaign,
        strategy_public_id=strategy_public_id,
        statement=payload.statement,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return hypothesis_to_public(hypothesis)


@router.post(
    "/hypotheses/{hypothesis_public_id}/experiments",
    response_model=ExperimentPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_experiment(
    campaign_public_id: str,
    hypothesis_public_id: str,
    payload: CreateExperimentRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ExperimentPublic:
    """Records a governed Experiment under the exact named Hypothesis
    (MVP-32A/-32A-R1, frozen contract). Any active workspace membership
    may call this (MEMBER+, matching Hypothesis creation's own precedent —
    an Experiment proposes, it never commits or mutates the authoritative
    Strategy/Positioning/Hypothesis state). Any Hypothesis status is
    eligible (OPEN, CONFIRMED, REFUTED), but the Hypothesis's own parent
    Strategy must still be the Campaign's current version
    (``ExperimentStrategyStaleError`` otherwise — never silently rebased
    to whatever is current now)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    experiment = StrategyService(db).create_experiment(
        campaign=campaign,
        hypothesis_public_id=hypothesis_public_id,
        description=payload.description,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    return experiment_to_public(experiment, hypothesis_public_id=hypothesis_public_id)


@router.post(
    "/experiments/{experiment_public_id}/definition-versions",
    response_model=ExperimentDefinitionPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def write_experiment_definition_version(
    campaign_public_id: str,
    experiment_public_id: str,
    payload: DeclareExperimentDefinitionRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ExperimentDefinitionPublic:
    """Declares (``base_version=0``) or revises (``base_version`` = current
    tip) an Experiment's comparison Definition (MVP-37, frozen MVP-37B).
    Full-state, append-only, idempotent on ``client_request_id``: 201 for a
    new version, 200 for a materially equal replay, 409
    ``IDEMPOTENCY_KEY_CONFLICT`` for the same key with a different request.
    Any active workspace membership may call this (MEMBER+, the same tier
    as Hypothesis/Experiment). Writing is not approval and not execution
    authorization; the Experiment's Strategy must still be current
    (``EXPERIMENT_DEFINITION_STRATEGY_STALE`` otherwise)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    version, created = ExperimentDefinitionService(db).write_version(
        campaign=campaign,
        experiment_public_id=experiment_public_id,
        base_version=payload.base_version,
        client_request_id=payload.client_request_id,
        fields={
            "comparison_question": payload.comparison_question,
            "comparison_type": payload.comparison_type.value,
            "changed_factor": payload.changed_factor,
            "controlled_factors": list(payload.controlled_factors),
            "comparison_basis": payload.comparison_basis,
            "scope": payload.scope,
            "learning_intent": payload.learning_intent,
            "non_conclusion_boundary": payload.non_conclusion_boundary,
        },
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    pin_state = ExperimentDefinitionService(db).pin_states_for_versions(
        workspace_id=campaign.workspace_id, definition_version_ids=[version.id]
    )[version.id]
    return definition_to_public(version, experiment_public_id=experiment_public_id, pin_state=pin_state)


@router.get(
    "/experiments/{experiment_public_id}/definition-versions",
    response_model=ExperimentDefinitionHistoryResponse,
)
async def get_experiment_definition_history(
    campaign_public_id: str,
    experiment_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ExperimentDefinitionHistoryResponse:
    """Ascending version history for one Experiment of this campaign —
    readable for any Experiment, including one under a superseded Strategy.
    No pagination (MVP37B-OBS-2)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    experiment, versions = ExperimentDefinitionService(db).get_history(
        campaign=campaign, experiment_public_id=experiment_public_id
    )
    tip = versions[-1] if versions else None
    pin_states = ExperimentDefinitionService(db).pin_states_for_versions(
        workspace_id=campaign.workspace_id, definition_version_ids=[v.id for v in versions]
    )
    return ExperimentDefinitionHistoryResponse(
        experiment_id=experiment.public_id,
        comparison_label=comparison_label_for(tip),
        current_version=tip.version if tip is not None else None,
        versions=[
            definition_to_public(v, experiment_public_id=experiment.public_id, pin_state=pin_states[v.id])
            for v in versions
        ],
    )


@router.post(
    "/experiments/{experiment_public_id}/variants",
    response_model=VariantPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def declare_experiment_variant(
    campaign_public_id: str,
    experiment_public_id: str,
    payload: CreateVariantRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> VariantPublic:
    """Declares one immutable Variant — the identity of ONE declared
    condition — pinned to the EXPLICITLY named current Definition version
    (MVP-38, frozen MVP-38B). Idempotent on ``client_request_id``: 201 for a
    new Variant, 200 for a materially equal replay, 409
    ``IDEMPOTENCY_KEY_CONFLICT`` for the same key with a different request.
    Any active workspace membership may call this (MEMBER+). Declaring a
    Variant pins the Definition version (no further version can be written)
    and is NOT allocation, exposure, measurement, a result or execution
    authorization. There is no PATCH/PUT/DELETE: a declared Variant cannot
    currently be corrected (MVP38A-OBS-3)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    variant, version, created = ExperimentVariantService(db).declare_variant(
        campaign=campaign,
        experiment_public_id=experiment_public_id,
        definition_version_public_id=payload.definition_version_id,
        label=payload.label,
        condition_description=payload.condition_description,
        client_request_id=payload.client_request_id,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return variant_to_public(
        variant, experiment_public_id=experiment_public_id, definition_version_public_id=version.public_id
    )


@router.get("/experiments/{experiment_public_id}/variants", response_model=VariantListResponse)
async def list_experiment_variants(
    campaign_public_id: str,
    experiment_public_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> VariantListResponse:
    """A deterministic page of the Experiment's declared Variants, ordered by
    the pinned Definition version's ordinal then the Variant's ordinal —
    never by ``created_at``. Readable for any Experiment of the campaign,
    including one under a superseded Strategy; a definition-less Experiment
    returns an empty page."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    experiment, items, total = ExperimentVariantService(db).list_variants(
        campaign=campaign, experiment_public_id=experiment_public_id, limit=limit, offset=offset
    )
    return VariantListResponse(
        experiment_id=experiment.public_id,
        items=[
            variant_to_public(
                variant, experiment_public_id=experiment.public_id, definition_version_public_id=version.public_id
            )
            for variant, version in items
        ],
        limit=limit,
        offset=offset,
        total=total,
    )
