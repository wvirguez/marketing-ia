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
    POST /api/v1/campaigns/{campaign_id}/experiments/{experiment_id}/measurement-contract
    GET  /api/v1/campaigns/{campaign_id}/experiments/{experiment_id}/measurement-contract
    GET  /api/v1/campaigns/{campaign_id}/experiments/{experiment_id}/measurement-contract/history
        (MVP-39, frozen MVP-39A/-39B) — the append-only, versioned PRE-
        EXECUTION measurement intent, pinned to the EXPLICITLY named current
        Definition version (declaring the first one derives the Definition
        lock, exactly like Variant — the two are independent siblings under
        the same lock). One route declares (``base_version=0``), revises
        (``base_version`` = current Contract tip) or replays; the GETs are
        the current tip (or ``null``) and the unpaginated version history.
        No freeze/execution/result endpoint exists — this domain implements
        none of those.
    POST /api/v1/campaigns/{campaign_id}/experiments/{experiment_id}/execution-authorization
    GET  /api/v1/campaigns/{campaign_id}/experiments/{experiment_id}/execution-authorization
    GET  /api/v1/campaigns/{campaign_id}/experiments/{experiment_id}/execution-authorization/history
    POST /api/v1/campaigns/{campaign_id}/experiments/{experiment_id}/execution-authorization/revoke
        (MVP-40, frozen Execution Authorization Design Freeze) — asserts only
        that ONE specific, immutable configuration (current Definition tip +
        the complete current Variant set + current Measurement Contract tip)
        has been authorized to begin future execution. The client never
        supplies which Definition/Contract/Variants — Authorization always
        pins whatever is current under lock. Idempotent on
        ``client_request_id`` only (no ``base_version``: an Authorization is
        never revised, only superseded). At most one ACTIVE Authorization
        exists per Experiment; a new valid request automatically supersedes
        the prior one. Revocation is one-way and requires a reason. No
        allocation/assignment/exposure/tracking-validation/result/winner
        endpoint or field exists anywhere in this surface.

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
from app.strategy.execution_authorization_service import ExperimentExecutionAuthorizationService
from app.strategy.measurement_contract_service import ExperimentMeasurementContractService
from app.strategy.models import ExecutionAuthorization, ExecutionAuthorizationVariant
from app.strategy.schemas import (
    AuthorizeExecutionRequest,
    CreateExperimentRequest,
    CreateHypothesisRequest,
    CreateVariantRequest,
    DeclareExperimentDefinitionRequest,
    DeclareMeasurementContractRequest,
    ExecutionAuthorizationHistoryResponse,
    ExecutionAuthorizationPublic,
    ExecutionAuthorizationVariantPublic,
    ExperimentDefinitionHistoryResponse,
    ExperimentDefinitionPublic,
    ExperimentPublic,
    HypothesisPublic,
    MeasurementContractHistoryResponse,
    MeasurementContractPublic,
    RevokeExecutionAuthorizationRequest,
    StrategyOutputResponse,
    VariantListResponse,
    VariantPublic,
    comparison_label_for,
    definition_to_public,
    execution_authorization_to_public,
    experiment_to_public,
    hypothesis_to_public,
    measurement_contract_label_for,
    measurement_contract_to_public,
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
                    pin_states[definition_tips[e.id].id]
                    if e.id in definition_tips
                    else (0, False, False, None)
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


@router.post(
    "/experiments/{experiment_public_id}/measurement-contract",
    response_model=MeasurementContractPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def write_measurement_contract(
    campaign_public_id: str,
    experiment_public_id: str,
    payload: DeclareMeasurementContractRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> MeasurementContractPublic:
    """Declares (``base_version=0``) or revises (``base_version`` = current
    Contract tip) an Experiment's PRE-EXECUTION Measurement Contract (MVP-39,
    frozen MVP-39B). Full-state, append-only, idempotent on
    ``client_request_id``: 201 for a new version, 200 for a materially equal
    replay, 409 ``IDEMPOTENCY_KEY_CONFLICT`` for the same key with a
    different request. Any active workspace membership may call this
    (MEMBER+, the same tier as Definition/Variant). The named
    ``definition_version_id`` must be the Experiment's current Definition
    tip (never silently rebased); declaring the first Contract version pins
    that Definition version, exactly like a Variant. Writing is not
    freezing, not execution authorization, and not a claim that evidence
    exists — there is no freeze endpoint in this domain."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    contract, signals, definition, created = ExperimentMeasurementContractService(db).declare_or_revise(
        campaign=campaign,
        experiment_public_id=experiment_public_id,
        base_version=payload.base_version,
        client_request_id=payload.client_request_id,
        definition_version_public_id=payload.definition_version_id,
        measurement_window_days=payload.measurement_window_days,
        minimum_evidence=payload.minimum_evidence,
        success_criterion=payload.success_criterion,
        analysis_method_intent=payload.analysis_method_intent,
        stopping_rule=payload.stopping_rule,
        decision_rule_intent=payload.decision_rule_intent,
        signals=[
            {
                "name": signal.name,
                "description": signal.description,
                "expected_direction": signal.expected_direction.value if signal.expected_direction else None,
                "evidence_requirement": signal.evidence_requirement,
                "tracking_required": signal.tracking_required,
            }
            for signal in payload.signals
        ],
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return measurement_contract_to_public(
        contract,
        experiment_public_id=experiment_public_id,
        definition_version_public_id=definition.public_id,
        signals=signals,
    )


@router.get(
    "/experiments/{experiment_public_id}/measurement-contract",
    response_model=MeasurementContractPublic | None,
)
async def get_measurement_contract(
    campaign_public_id: str,
    experiment_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> MeasurementContractPublic | None:
    """The current Measurement Contract tip, or ``null`` if none has been
    declared. Readable for any Experiment of the campaign, including one
    under a superseded Strategy."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    experiment, tip, signals = ExperimentMeasurementContractService(db).get_current(
        campaign=campaign, experiment_public_id=experiment_public_id
    )
    if tip is None:
        return None
    definition = ExperimentDefinitionService(db).definitions.get_by_id(tip.definition_version_id)
    return measurement_contract_to_public(
        tip,
        experiment_public_id=experiment.public_id,
        definition_version_public_id=definition.public_id if definition is not None else "",
        signals=signals,
    )


@router.get(
    "/experiments/{experiment_public_id}/measurement-contract/history",
    response_model=MeasurementContractHistoryResponse,
)
async def get_measurement_contract_history(
    campaign_public_id: str,
    experiment_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> MeasurementContractHistoryResponse:
    """Ascending version history for one Experiment's Measurement Contract —
    readable for any Experiment, including one under a superseded Strategy.
    No pagination (mirrors the Definition history route's own accepted
    MVP37B-OBS-2 limit)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    experiment, versions = ExperimentMeasurementContractService(db).get_history(
        campaign=campaign, experiment_public_id=experiment_public_id
    )
    tip = versions[-1][0] if versions else None
    definitions = ExperimentDefinitionService(db).definitions
    return MeasurementContractHistoryResponse(
        experiment_id=experiment.public_id,
        measurement_contract_label=measurement_contract_label_for(tip),
        current_version=tip.version if tip is not None else None,
        versions=[
            measurement_contract_to_public(
                version,
                experiment_public_id=experiment.public_id,
                definition_version_public_id=(
                    definition.public_id
                    if (definition := definitions.get_by_id(version.definition_version_id)) is not None
                    else ""
                ),
                signals=signals,
            )
            for version, signals in versions
        ],
    )


def _execution_authorization_to_public(
    db: Session,
    authorization: ExecutionAuthorization,
    snapshot_rows: list[ExecutionAuthorizationVariant],
    *,
    experiment_public_id: str,
    superseded_by_public_id: str | None,
) -> ExecutionAuthorizationPublic:
    """MVP-40: resolves the pinned Definition/Contract/Variant-set back to
    their public ids for one Authorization row (no N+1 across a history
    listing, since callers batch-load the Variant set once per row)."""
    definition = ExperimentDefinitionService(db).definitions.get_by_id(authorization.definition_version_id)
    contract = ExperimentMeasurementContractService(db).contracts.get_by_id(authorization.contract_version_id)
    variant_rows = ExperimentVariantService(db).variants.list_by_ids(
        variant_ids=[row.variant_id for row in snapshot_rows]
    )
    variants_by_id = {variant.id: variant for variant in variant_rows}
    signals = (
        ExperimentMeasurementContractService(db).contracts.list_signals_for_version(contract_version_id=contract.id)
        if contract is not None
        else []
    )
    return execution_authorization_to_public(
        authorization,
        experiment_public_id=experiment_public_id,
        definition_version_public_id=definition.public_id if definition is not None else "",
        contract_version_public_id=contract.public_id if contract is not None else "",
        variants=[
            ExecutionAuthorizationVariantPublic(
                id=variant.public_id, label=variant.label, condition_description=variant.condition_description
            )
            for row in snapshot_rows
            if (variant := variants_by_id.get(row.variant_id)) is not None
        ],
        signal_count=len(signals),
        tracking_required_signal_count=sum(1 for signal in signals if signal.tracking_required),
        superseded_by_public_id=superseded_by_public_id,
    )


@router.post(
    "/experiments/{experiment_public_id}/execution-authorization",
    response_model=ExecutionAuthorizationPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def authorize_execution(
    campaign_public_id: str,
    experiment_public_id: str,
    payload: AuthorizeExecutionRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ExecutionAuthorizationPublic:
    """Authorizes THIS SPECIFIC, current configuration (Experiment + current
    Definition tip + the complete current Variant set + current Measurement
    Contract tip) to begin future execution (MVP-40, frozen Execution
    Authorization Design Freeze). Idempotent on ``client_request_id``: 201
    for a new Authorization, 200 for a materially equal replay, 409
    ``IDEMPOTENCY_KEY_CONFLICT`` for the same key against different or
    since-changed material. A new valid request automatically supersedes
    any prior active Authorization for the same Experiment — at most one
    ACTIVE Authorization ever exists per Experiment. Any active workspace
    membership may call this (MEMBER+, the same tier as Definition/Variant/
    Contract). AUTHORIZATION != EXECUTION: it does not mean assignment,
    allocation, delivery, exposure, tracking validation, measurement,
    evidence, a result, or a winner exists."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    authorization, snapshot_rows, created = ExperimentExecutionAuthorizationService(db).authorize(
        campaign=campaign,
        experiment_public_id=experiment_public_id,
        client_request_id=payload.client_request_id,
        unit_of_assignment=payload.unit_of_assignment,
        allocation_design=payload.allocation_design,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _execution_authorization_to_public(
        db, authorization, snapshot_rows, experiment_public_id=experiment_public_id, superseded_by_public_id=None
    )


@router.get(
    "/experiments/{experiment_public_id}/execution-authorization",
    response_model=ExecutionAuthorizationPublic | None,
)
async def get_execution_authorization(
    campaign_public_id: str,
    experiment_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ExecutionAuthorizationPublic | None:
    """The current ACTIVE Execution Authorization, or ``null`` if none is
    active. Readable for any Experiment of the campaign, including one
    under a superseded Strategy."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    experiment, active, snapshot_rows = ExperimentExecutionAuthorizationService(db).get_current(
        campaign=campaign, experiment_public_id=experiment_public_id
    )
    if active is None:
        return None
    return _execution_authorization_to_public(
        db, active, snapshot_rows, experiment_public_id=experiment.public_id, superseded_by_public_id=None
    )


@router.get(
    "/experiments/{experiment_public_id}/execution-authorization/history",
    response_model=ExecutionAuthorizationHistoryResponse,
)
async def get_execution_authorization_history(
    campaign_public_id: str,
    experiment_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ExecutionAuthorizationHistoryResponse:
    """Ascending, unpaginated history — every Authorization ever created for
    this Experiment, active and revoked alike (mirrors the Contract history
    route's own accepted MVP37B-OBS-2 limit)."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    experiment, rows = ExperimentExecutionAuthorizationService(db).get_history(
        campaign=campaign, experiment_public_id=experiment_public_id
    )
    public_ids_by_id = {authorization.id: authorization.public_id for authorization, _ in rows}
    current = next((authorization for authorization, _ in rows if authorization.revoked_at is None), None)
    return ExecutionAuthorizationHistoryResponse(
        experiment_id=experiment.public_id,
        current_id=current.public_id if current is not None else None,
        authorizations=[
            _execution_authorization_to_public(
                db,
                authorization,
                snapshot_rows,
                experiment_public_id=experiment.public_id,
                superseded_by_public_id=(
                    public_ids_by_id.get(authorization.superseded_by_execution_authorization_id)
                    if authorization.superseded_by_execution_authorization_id is not None
                    else None
                ),
            )
            for authorization, snapshot_rows in rows
        ],
    )


@router.post(
    "/experiments/{experiment_public_id}/execution-authorization/revoke",
    response_model=ExecutionAuthorizationPublic,
    dependencies=[Depends(require_csrf)],
)
async def revoke_execution_authorization(
    campaign_public_id: str,
    experiment_public_id: str,
    payload: RevokeExecutionAuthorizationRequest,
    request: Request,
    user: User = Depends(get_current_user),
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ExecutionAuthorizationPublic:
    """Revokes the current ACTIVE Execution Authorization for this Experiment
    (MVP-40, frozen §L/§19) — one-way, non-reversible. A reason is required.
    409 ``EXECUTION_AUTHORIZATION_NONE_ACTIVE`` if none is currently active.
    Revocation withdraws authority to begin/continue future execution only —
    it never means past assignment/exposure/evidence did not occur."""
    campaign = CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )
    authorization = ExperimentExecutionAuthorizationService(db).revoke(
        campaign=campaign,
        experiment_public_id=experiment_public_id,
        reason=payload.reason,
        actor_user_id=user.id,
        request_id=request.state.request_id,
    )
    snapshot_rows = ExperimentExecutionAuthorizationService(db).authorizations.list_variants_for_authorization(
        authorization_id=authorization.id
    )
    return _execution_authorization_to_public(
        db, authorization, snapshot_rows, experiment_public_id=experiment_public_id, superseded_by_public_id=None
    )
