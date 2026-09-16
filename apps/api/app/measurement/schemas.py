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

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.content.models import ContentDistribution, ContentPiece
from app.measurement.models import (
    AnalysisResult,
    DistributionMetricEvidence,
    MeasurementAnalysisRun,
    MetricEntry,
    MetricSource,
    MetricValue,
    PerformanceObservation,
    PerformanceSignal,
)

# MVP-19B §15/§76: shared validation for both the create and correction
# Evidence request bodies — stricter than MetricEntryWriteRequest.values
# (which BACKEND-11 never required to reject duplicates-after-trim,
# oversized names, non-finite values, or NUMERIC(20,4) overflow), and
# deliberately NOT retrofitted onto MetricEntryWriteRequest itself, to
# keep this a purely additive change with zero regression risk to the
# existing, already-shipped Metric Entry contract.
_EVIDENCE_METRIC_NAME_MAX_LENGTH = 100
_EVIDENCE_METRIC_VALUE_MAX_DIGITS = 20
_EVIDENCE_METRIC_VALUE_MAX_DECIMALS = 4


def _validate_evidence_metric_values(raw: dict[str, Decimal]) -> dict[str, Decimal]:
    cleaned: dict[str, Decimal] = {}
    for raw_name, value in raw.items():
        name = raw_name.strip()
        if not name:
            raise ValueError("Metric names must not be empty.")
        if len(name) > _EVIDENCE_METRIC_NAME_MAX_LENGTH:
            raise ValueError(f"Metric names must be at most {_EVIDENCE_METRIC_NAME_MAX_LENGTH} characters.")
        if name in cleaned:
            raise ValueError("Duplicate metric name after trimming.")
        if not value.is_finite():
            raise ValueError("Metric values must be finite.")
        _sign, digits, exponent = value.as_tuple()
        if not isinstance(exponent, int):
            raise ValueError("Metric values must be finite.")
        if exponent > 0:
            raise ValueError("Metric values must not use a positive exponent.")
        decimal_places = -exponent
        if decimal_places > _EVIDENCE_METRIC_VALUE_MAX_DECIMALS:
            raise ValueError(f"Metric values must have at most {_EVIDENCE_METRIC_VALUE_MAX_DECIMALS} decimal places.")
        integer_digits = len(digits) - decimal_places
        if integer_digits > (_EVIDENCE_METRIC_VALUE_MAX_DIGITS - _EVIDENCE_METRIC_VALUE_MAX_DECIMALS):
            raise ValueError("Metric value exceeds the supported numeric range.")
        cleaned[name] = value
    if not cleaned:
        raise ValueError("At least one metric value is required.")
    return cleaned


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


class MeasurementAnalysisRunTriggerRequest(BaseModel):
    """MVP-11C-A/-R1: ``client_request_id`` is an opaque, client-owned
    idempotency key, unique per logical analysis-trigger operation across
    the *entire workspace* (never merely per campaign) — matching
    ``MetricEntryWriteRequest.client_request_id`` exactly, including its
    length constraint. Reusing this key for the SAME campaign returns the
    original run unchanged rather than executing again. Reusing it for a
    DIFFERENT campaign in the same workspace is not a valid replay — it is
    a key-scope collision, rejected with HTTP 409 (``IDEMPOTENCY_KEY_
    CONFLICT``) and never executes a second run. Use a fresh key for every
    new logical execution."""

    client_request_id: str = Field(min_length=1, max_length=100)


class MeasurementAnalysisRunPublic(BaseModel):
    id: str
    campaign_id: str
    client_request_id: str
    status: str
    failure_reason: str | None
    created_at: datetime
    completed_at: datetime | None


def measurement_analysis_run_to_public(
    run: MeasurementAnalysisRun, *, campaign_public_id: str
) -> MeasurementAnalysisRunPublic:
    """``campaign_public_id`` must come from the caller's own already-
    authorized campaign, never from ``run`` itself — the router is
    responsible for proving ``run.campaign_id == authorized_campaign.id``
    before this converter is ever invoked (MVP-11C-A-R1's resource
    invariant), so this function never silently serializes a mismatched
    campaign."""
    return MeasurementAnalysisRunPublic(
        id=run.public_id,
        campaign_id=campaign_public_id,
        client_request_id=run.client_request_id,
        status=run.status.value,
        failure_reason=run.failure_reason,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


# --- Distribution-linked Measurement Evidence — MVP-19B ---------------------


class DistributionEvidenceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: date
    period_end: date
    values: dict[str, Decimal] = Field(min_length=1)
    client_request_id: str = Field(min_length=1, max_length=100)
    source_reference: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def _validate(self) -> "DistributionEvidenceCreateRequest":
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start.")
        if self.period_end > datetime.now(timezone.utc).date():
            raise ValueError("period_end must not be in the future.")
        self.values = _validate_evidence_metric_values(self.values)
        return self


class DistributionEvidenceCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: date
    period_end: date
    values: dict[str, Decimal] = Field(min_length=1)
    client_request_id: str = Field(min_length=1, max_length=100)
    source_reference: str | None = Field(default=None, max_length=2048)
    correction_reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _validate(self) -> "DistributionEvidenceCorrectionRequest":
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start.")
        if self.period_end > datetime.now(timezone.utc).date():
            raise ValueError("period_end must not be in the future.")
        self.values = _validate_evidence_metric_values(self.values)
        trimmed_reason = self.correction_reason.strip()
        if not trimmed_reason:
            raise ValueError("correction_reason must not be empty.")
        self.correction_reason = trimmed_reason
        return self


class DistributionEvidencePublic(BaseModel):
    id: str
    distribution_id: str
    content_piece_id: str
    metric_entry_id: str
    evidence_scope: Literal["DISTRIBUTION_SPECIFIC"] = "DISTRIBUTION_SPECIFIC"
    values: dict[str, Decimal]
    period_start: date
    period_end: date
    channel: str
    source: MetricSource
    source_reference: str | None
    reported_by: str | None
    reported_at: datetime
    is_current: bool
    supersedes_evidence_id: str | None
    correction_reason: str | None


class DistributionEvidenceListResponse(BaseModel):
    items: list[DistributionEvidencePublic]
    limit: int
    offset: int
    total: int


def distribution_evidence_to_public(
    evidence: DistributionMetricEvidence,
    *,
    entry: MetricEntry,
    values: list[MetricValue],
    distribution_public_id: str,
    content_piece_public_id: str,
    is_current: bool,
    supersedes_evidence_public_id: str | None,
    reporter_public_id: str | None,
) -> DistributionEvidencePublic:
    return DistributionEvidencePublic(
        id=evidence.public_id,
        distribution_id=distribution_public_id,
        content_piece_id=content_piece_public_id,
        metric_entry_id=entry.public_id,
        values={v.metric_name: v.value for v in values},
        period_start=entry.period_start,
        period_end=entry.period_end,
        channel=entry.channel,
        source=entry.source,
        source_reference=evidence.source_reference,
        reported_by=reporter_public_id,
        reported_at=evidence.created_at,
        is_current=is_current,
        supersedes_evidence_id=supersedes_evidence_public_id,
        correction_reason=evidence.correction_reason,
    )


# MVP-21 (frozen by MVP-21A/MVP-21A-R1): a read-only, descriptive,
# non-causal, per-Distribution summary over current (non-superseded)
# Evidence only. Deliberately excludes SUM/AVERAGE/MIN/MAX/TREND/PERCENT
# CHANGE — metric_name is free text with no type metadata anywhere in this
# schema, so no arithmetic aggregation across independent reports can be
# proven safe (MVP-21A §I-M). Only cardinality (report_count) and pure
# selection by reporting chronology (latest/earliest) are implemented.


class MetricSummaryItem(BaseModel):
    metric_name: str
    report_count: int
    latest_value: Decimal
    latest_period_start: date
    latest_period_end: date
    latest_reported_at: datetime
    earliest_value: Decimal
    earliest_reported_at: datetime


class DistributionEvidenceSummaryPublic(BaseModel):
    content_piece_id: str
    distribution_id: str | None
    content_version_id: str | None
    channel: str | None
    metrics: list[MetricSummaryItem]


def build_distribution_evidence_summary(
    *,
    content_piece_id: str,
    distribution_id: str | None,
    content_version_id: str | None,
    channel: str | None,
    current_rows: list[tuple[DistributionMetricEvidence, MetricEntry, list[MetricValue]]],
) -> DistributionEvidenceSummaryPublic:
    """Pure aggregation over an already-resolved current-Evidence set (no
    DB access here — ``current_rows`` must already come from
    ``MeasurementService.summarize_distribution_evidence``, which is the
    sole caller of the single-statement ``list_current_for_distribution``
    query). Grouping key is the exact stored ``metric_name`` — never
    case-folded (MVP-21A §G/§AH: "Clicks" and "clicks" remain distinct).

    LATEST = maximum by (evidence.created_at, evidence.id); EARLIEST =
    minimum by the same pair — ``id`` is an internal tie-break only and is
    never exposed on ``MetricSummaryItem`` (MVP-21A-R1 §L/§M/§U)."""
    counts: dict[str, int] = {}
    latest: dict[str, tuple[datetime, uuid.UUID, MetricEntry, DistributionMetricEvidence, Decimal]] = {}
    earliest: dict[str, tuple[datetime, uuid.UUID, MetricEntry, DistributionMetricEvidence, Decimal]] = {}
    for evidence, entry, values in current_rows:
        tie_break = (evidence.created_at, evidence.id)
        for value in values:
            name = value.metric_name
            counts[name] = counts.get(name, 0) + 1
            if name not in latest or tie_break > latest[name][:2]:
                latest[name] = (*tie_break, entry, evidence, value.value)
            if name not in earliest or tie_break < earliest[name][:2]:
                earliest[name] = (*tie_break, entry, evidence, value.value)
    metrics = [
        MetricSummaryItem(
            metric_name=name,
            report_count=counts[name],
            latest_value=latest[name][4],
            latest_period_start=latest[name][2].period_start,
            latest_period_end=latest[name][2].period_end,
            latest_reported_at=latest[name][3].created_at,
            earliest_value=earliest[name][4],
            earliest_reported_at=earliest[name][3].created_at,
        )
        for name in sorted(counts)
    ]
    return DistributionEvidenceSummaryPublic(
        content_piece_id=content_piece_id,
        distribution_id=distribution_id,
        content_version_id=content_version_id,
        channel=channel,
        metrics=metrics,
    )


# MVP-22 (frozen by MVP-22A): a read-only, Campaign-wide, descriptive,
# non-causal rollup over current (non-superseded) Evidence across every
# eligible Distribution in the Campaign. Same forbidden-arithmetic boundary
# as MVP-21 (no SUM/AVERAGE/TREND/etc.) — compounded, not relaxed, by
# spanning multiple Distributions. This is a genuinely new, additive
# schema family; MVP-21's own DistributionEvidenceSummaryPublic/
# MetricSummaryItem contract is untouched.


class EvidenceObservation(BaseModel):
    value: Decimal
    period_start: date
    period_end: date
    reported_at: datetime
    content_piece_id: str
    distribution_id: str
    content_version_id: str
    channel: str


class CampaignMetricSummaryItem(BaseModel):
    metric_name: str
    report_count: int
    latest: EvidenceObservation
    earliest: EvidenceObservation


class CampaignDistributionEvidenceRollupPublic(BaseModel):
    campaign_id: str
    metrics: list[CampaignMetricSummaryItem]


def _campaign_evidence_observation(
    *,
    entry: MetricEntry,
    value: Decimal,
    reported_at: datetime,
    distribution: ContentDistribution,
    piece: ContentPiece,
    content_version_public_ids: dict[uuid.UUID, str],
) -> EvidenceObservation:
    return EvidenceObservation(
        value=value,
        period_start=entry.period_start,
        period_end=entry.period_end,
        reported_at=reported_at,
        content_piece_id=piece.public_id,
        distribution_id=distribution.public_id,
        content_version_id=content_version_public_ids[distribution.content_version_id],
        channel=distribution.channel,
    )


def build_campaign_distribution_evidence_rollup(
    *,
    campaign_id: str,
    current_rows: list[tuple[DistributionMetricEvidence, ContentDistribution, ContentPiece, MetricEntry, list[MetricValue]]],
    content_version_public_ids: dict[uuid.UUID, str],
) -> CampaignDistributionEvidenceRollupPublic:
    """Pure aggregation over an already-resolved, Campaign-wide
    current-Evidence set (no DB access here — ``current_rows`` must
    already come from
    ``MeasurementService.summarize_distribution_evidence_for_campaign``,
    the sole caller of the single-statement ``list_current_for_campaign``
    query, and ``content_version_public_ids`` must already be a batched
    resolution of every distinct ``ContentDistribution.content_version_id``
    referenced by ``current_rows`` — never a per-observation query).

    Grouping key is the exact stored ``metric_name`` — never case-folded.
    LATEST = maximum by (evidence.created_at, evidence.id); EARLIEST =
    minimum by the same pair, exactly as MVP-21 freezes — ``id`` is an
    internal tie-break only and is never exposed on ``EvidenceObservation``.
    """
    counts: dict[str, int] = {}
    latest: dict[str, tuple] = {}
    earliest: dict[str, tuple] = {}
    for evidence, distribution, piece, entry, values in current_rows:
        tie_break = (evidence.created_at, evidence.id)
        for value in values:
            name = value.metric_name
            counts[name] = counts.get(name, 0) + 1
            candidate = (*tie_break, value.value, entry, distribution, piece)
            if name not in latest or tie_break > latest[name][:2]:
                latest[name] = candidate
            if name not in earliest or tie_break < earliest[name][:2]:
                earliest[name] = candidate
    metrics = [
        CampaignMetricSummaryItem(
            metric_name=name,
            report_count=counts[name],
            latest=_campaign_evidence_observation(
                entry=latest[name][3], value=latest[name][2], reported_at=latest[name][0],
                distribution=latest[name][4], piece=latest[name][5], content_version_public_ids=content_version_public_ids,
            ),
            earliest=_campaign_evidence_observation(
                entry=earliest[name][3], value=earliest[name][2], reported_at=earliest[name][0],
                distribution=earliest[name][4], piece=earliest[name][5], content_version_public_ids=content_version_public_ids,
            ),
        )
        for name in sorted(counts)
    ]
    return CampaignDistributionEvidenceRollupPublic(campaign_id=campaign_id, metrics=metrics)
