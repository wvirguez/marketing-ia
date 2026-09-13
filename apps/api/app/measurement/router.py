"""Measurement API surface (BACKEND-11 §10/§11/§12/§13/§15;
MVP-11C-A/-R1/-B for the analysis trigger) — exactly:

    GET  /api/v1/campaigns/{campaign_id}/metrics
    POST /api/v1/campaigns/{campaign_id}/metrics
    PUT  /api/v1/campaigns/{campaign_id}/metrics
    GET  /api/v1/campaigns/{campaign_id}/analysis
    POST /api/v1/campaigns/{campaign_id}/analysis/run

POST/PUT are the first genuinely public writes in this bounded-context
review series that are not deferred pending a governance-authority
question — ordinary authenticated workspace membership is sufficient here
(no approval concept exists for Metric Entry), so both require only
``require_csrf`` on top of the usual tenant resolution, the same shape
``app/campaigns/router.py`` already uses for its own mutating routes.

``POST /analysis/run`` triggers the already-implemented, synchronous
``MeasurementAnalysisService.run_analysis`` (MVP-11B) — same auth/CSRF/
tenancy shape, plus the MVP-11C-A-R1 cross-campaign idempotency repair:
``client_request_id`` is scoped to the whole workspace (matching
MetricEntry's own precedent), never to one campaign, so this route must
never return another campaign's ``MeasurementAnalysisRun`` merely because
its key collided with one already used elsewhere — see
``trigger_analysis_run``'s own docstring below for the exact contract.

No route for Observation/Signal/Analysis Result creation exists — those
remain service-layer-only (``app/measurement/service.py``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, get_current_workspace, require_csrf
from app.campaigns.service import CampaignAccessService
from app.core.api_errors import IdempotencyKeyConflictError
from app.measurement.analysis_pipeline import MeasurementAnalysisService
from app.measurement.models import MeasurementAnalysisRunStatus
from app.measurement.repository import MeasurementAnalysisRunRepository
from app.measurement.schemas import (
    AnalysisResponse,
    MeasurementAnalysisRunPublic,
    MeasurementAnalysisRunTriggerRequest,
    MetricEntryListResponse,
    MetricEntryPublic,
    MetricEntryWriteRequest,
    analysis_result_to_public,
    measurement_analysis_run_to_public,
    metric_entry_to_public,
    observation_to_public,
    signal_to_public,
)
from app.measurement.service import MeasurementService
from app.persistence.session import get_db
from app.users.models import User
from app.workspaces.models import Workspace

router = APIRouter(prefix="/campaigns/{campaign_public_id}", tags=["measurement"])


def _authorized_campaign(campaign_public_id: str, workspace: Workspace, db: Session):
    return CampaignAccessService(db).get_authorized_campaign(
        workspace_id=workspace.id, campaign_public_id=campaign_public_id
    )


@router.get("/metrics", response_model=MetricEntryListResponse)
async def list_metrics(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> MetricEntryListResponse:
    campaign = _authorized_campaign(campaign_public_id, workspace, db)
    rows = MeasurementService(db).list_metric_entries_for_campaign(campaign_id=campaign.id)
    return MetricEntryListResponse(
        items=[metric_entry_to_public(entry, values=values, is_current=is_current) for entry, values, is_current in rows]
    )


@router.post("/metrics", response_model=MetricEntryPublic, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_csrf)])
async def create_metric_entry(
    campaign_public_id: str,
    payload: MetricEntryWriteRequest,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> MetricEntryPublic:
    campaign = _authorized_campaign(campaign_public_id, workspace, db)
    service = MeasurementService(db)
    entry = service.record_metric_entry(
        campaign=campaign, period_start=payload.period_start, period_end=payload.period_end,
        channel=payload.channel, source=payload.source, client_request_id=payload.client_request_id,
        metric_values=payload.values, is_correction=False,
    )
    values = service.values.list_for_entry(entry.id)
    return metric_entry_to_public(entry, values=values, is_current=True)


@router.put("/metrics", response_model=MetricEntryPublic, dependencies=[Depends(require_csrf)])
async def correct_metric_entry(
    campaign_public_id: str,
    payload: MetricEntryWriteRequest,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> MetricEntryPublic:
    """APPEND CORRECTED REPLACEMENT — never a SQL UPDATE. Creates a new,
    independent, immutable Metric Entry sharing the same logical grouping
    as any prior entry; the prior row is never read or touched."""
    campaign = _authorized_campaign(campaign_public_id, workspace, db)
    service = MeasurementService(db)
    entry = service.record_metric_entry(
        campaign=campaign, period_start=payload.period_start, period_end=payload.period_end,
        channel=payload.channel, source=payload.source, client_request_id=payload.client_request_id,
        metric_values=payload.values, is_correction=True,
    )
    values = service.values.list_for_entry(entry.id)
    return metric_entry_to_public(entry, values=values, is_current=True)


@router.get("/analysis", response_model=AnalysisResponse)
async def get_analysis(
    campaign_public_id: str,
    workspace: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> AnalysisResponse:
    campaign = _authorized_campaign(campaign_public_id, workspace, db)
    service = MeasurementService(db)
    observations, signals, results = service.get_analysis_for_campaign(campaign_id=campaign.id)

    entries_by_id = {e.id: e for e, _values, _is_current in service.list_metric_entries_for_campaign(campaign_id=campaign.id)}
    observations_by_id = {o.id: o for o in observations}
    signals_by_id = {s.id: s for s in signals}

    observation_public = [
        observation_to_public(
            o, source_public_ids=[entries_by_id[eid].public_id for eid in service.get_source_metric_entry_ids(o.id) if eid in entries_by_id]
        )
        for o in observations
    ]
    signal_public = [
        signal_to_public(
            s,
            source_public_ids=[
                observations_by_id[oid].public_id for oid in service.get_source_observation_ids(s.id) if oid in observations_by_id
            ],
        )
        for s in signals
    ]
    analysis_result_public = [
        analysis_result_to_public(
            r, source_public_ids=[signals_by_id[sid].public_id for sid in service.get_source_signal_ids(r.id) if sid in signals_by_id]
        )
        for r in results
    ]
    return AnalysisResponse(observations=observation_public, signals=signal_public, analysis_results=analysis_result_public)


@router.post("/analysis/run", response_model=MeasurementAnalysisRunPublic, dependencies=[Depends(require_csrf)])
async def trigger_analysis_run(
    campaign_public_id: str,
    payload: MeasurementAnalysisRunTriggerRequest,
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeasurementAnalysisRunPublic:
    """Synchronous trigger for the deterministic Measurement Analysis
    pipeline (MVP-11B). Always returns HTTP 200 — RUNNING/COMPLETED/FAILED
    are all valid, non-error resource states, never mapped onto an error
    status code.

    IDEMPOTENCY (MVP-11C-A/-R1): ``client_request_id`` is scoped to the
    whole workspace, never to one campaign — the same shape MetricEntry's
    own ``client_request_id`` already uses. Reusing the same key for THIS
    SAME campaign returns the original run unchanged (no re-execution, no
    duplicate evidence). Reusing it for a DIFFERENT campaign in this
    workspace is not a valid replay — it is a key-scope collision, and is
    rejected with HTTP 409 (``IDEMPOTENCY_KEY_CONFLICT``); it never
    executes a second run and never returns the other campaign's run.
    Every 2xx response from this route satisfies
    ``response.campaign_id == campaign_public_id`` (the URL's own
    campaign), with no exception — this is proven again immediately below,
    independent of whichever internal path produced ``run``, since
    ``MeasurementAnalysisRun`` idempotency is enforced at the
    ``(workspace_id, client_request_id)`` level, not per campaign.

    FAILURE TRANSLATION: ``MeasurementAnalysisService.run_analysis``
    persists a FAILED run and then re-raises the original processing
    exception (MVP-11B's own, unchanged contract — the exception is the
    correct signal for a direct service caller). This route is the
    boundary that reconciles that with "a persisted domain outcome is not
    a transport failure": on any exception, it re-queries the run by
    ``(workspace_id, client_request_id)`` and returns it as a normal 200
    only when it belongs to THIS campaign and is durably FAILED. Any other
    outcome (no run at all, a run belonging to another campaign, or a
    same-campaign run that is unexpectedly still RUNNING/COMPLETED despite
    the exception — which no legitimate single-fault source path
    produces) is never silently converted into a false success.
    """
    campaign = _authorized_campaign(campaign_public_id, workspace, db)
    analysis_service = MeasurementAnalysisService(db)
    run_repository = MeasurementAnalysisRunRepository(db)

    try:
        run = analysis_service.run_analysis(
            campaign=campaign,
            client_request_id=payload.client_request_id,
            actor_user_id=user.id,
            request_id=request.state.request_id,
        )
    except Exception:
        existing = run_repository.get_by_workspace_and_request_id(
            workspace_id=workspace.id, client_request_id=payload.client_request_id
        )
        if existing is None:
            # No resource was ever persisted for this key — a genuine,
            # unrecoverable failure with nothing to return.
            raise
        if existing.campaign_id != campaign.id:
            # This key was already used by a different campaign in this
            # workspace — never return that campaign's run here.
            raise IdempotencyKeyConflictError()
        if existing.status == MeasurementAnalysisRunStatus.FAILED:
            run = existing
        else:
            # A same-campaign run that is still RUNNING/COMPLETED despite
            # run_analysis raising has no legitimate source path (see the
            # docstring above) — never mask this as a false success.
            raise

    if run.campaign_id != campaign.id:
        # Covers both a sequential cross-campaign key reuse and a
        # concurrent race where this request's own call was resolved
        # (without raising) to another campaign's already-committed run.
        raise IdempotencyKeyConflictError()

    return measurement_analysis_run_to_public(run, campaign_public_id=campaign.public_id)
