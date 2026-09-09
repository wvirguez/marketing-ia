"""Measurement API surface (BACKEND-11 §10/§11/§12/§13/§15) — exactly:

    GET  /api/v1/campaigns/{campaign_id}/metrics
    POST /api/v1/campaigns/{campaign_id}/metrics
    PUT  /api/v1/campaigns/{campaign_id}/metrics
    GET  /api/v1/campaigns/{campaign_id}/analysis

POST/PUT are the first genuinely public writes in this bounded-context
review series that are not deferred pending a governance-authority
question — ordinary authenticated workspace membership is sufficient here
(no approval concept exists for Metric Entry), so both require only
``require_csrf`` on top of the usual tenant resolution, the same shape
``app/campaigns/router.py`` already uses for its own mutating routes.

No route for Observation/Signal/Analysis Result creation exists — those
remain service-layer-only (``app/measurement/service.py``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_workspace, require_csrf
from app.campaigns.service import CampaignAccessService
from app.measurement.schemas import (
    AnalysisResponse,
    MetricEntryListResponse,
    MetricEntryPublic,
    MetricEntryWriteRequest,
    analysis_result_to_public,
    metric_entry_to_public,
    observation_to_public,
    signal_to_public,
)
from app.measurement.service import MeasurementService
from app.persistence.session import get_db
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
