"""Public DTOs for the Measurement API. Never expose an internal UUID, a
raw ``workspace_id``, or an agent identifier — only public_id-derived
fields.

Request-side validation (period ordering, non-empty metric values, closed
``MetricSource`` vocabulary) happens here, at the API boundary, via
Pydantic — matching the established "every write DTO is validated at the
API boundary before it reaches any domain service" convention
(`docs/backend/BACKEND-01-ARCHITECTURE.md` §6).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.measurement.models import AnalysisResult, MetricEntry, MetricSource, MetricValue, PerformanceObservation, PerformanceSignal


class MetricEntryWriteRequest(BaseModel):
    period_start: date
    period_end: date
    channel: str = Field(min_length=1, max_length=100)
    source: MetricSource
    client_request_id: str = Field(min_length=1, max_length=100)
    values: dict[str, Decimal] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_period_order(self) -> "MetricEntryWriteRequest":
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start.")
        return self


class MetricEntryPublic(BaseModel):
    id: str
    period_start: date
    period_end: date
    channel: str
    source: MetricSource
    values: dict[str, Decimal]
    is_current: bool
    created_at: datetime


class MetricEntryListResponse(BaseModel):
    items: list[MetricEntryPublic]


class PerformanceObservationPublic(BaseModel):
    id: str
    metric_name: str
    value: Decimal
    source_metric_entry_ids: list[str]
    created_at: datetime


class PerformanceSignalPublic(BaseModel):
    id: str
    summary: str
    source_observation_ids: list[str]
    created_at: datetime


class AnalysisResultPublic(BaseModel):
    id: str
    summary: str
    source_signal_ids: list[str]
    created_at: datetime


class AnalysisResponse(BaseModel):
    observations: list[PerformanceObservationPublic]
    signals: list[PerformanceSignalPublic]
    analysis_results: list[AnalysisResultPublic]


def metric_entry_to_public(entry: MetricEntry, *, values: list[MetricValue], is_current: bool) -> MetricEntryPublic:
    return MetricEntryPublic(
        id=entry.public_id,
        period_start=entry.period_start,
        period_end=entry.period_end,
        channel=entry.channel,
        source=entry.source,
        values={v.metric_name: v.value for v in values},
        is_current=is_current,
        created_at=entry.created_at,
    )


def observation_to_public(observation: PerformanceObservation, *, source_public_ids: list[str]) -> PerformanceObservationPublic:
    return PerformanceObservationPublic(
        id=observation.public_id,
        metric_name=observation.metric_name,
        value=observation.value,
        source_metric_entry_ids=source_public_ids,
        created_at=observation.created_at,
    )


def signal_to_public(signal: PerformanceSignal, *, source_public_ids: list[str]) -> PerformanceSignalPublic:
    return PerformanceSignalPublic(
        id=signal.public_id, summary=signal.summary, source_observation_ids=source_public_ids, created_at=signal.created_at
    )


def analysis_result_to_public(result: AnalysisResult, *, source_public_ids: list[str]) -> AnalysisResultPublic:
    return AnalysisResultPublic(
        id=result.public_id, summary=result.summary, source_signal_ids=source_public_ids, created_at=result.created_at
    )
