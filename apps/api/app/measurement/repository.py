"""Data access for Measurement. No repository here calls
``session.commit()`` — see ``app/persistence/session.py`` and
``app/measurement/service.py`` for the transaction-ownership boundary.

Every ``create``/``create_many`` method takes the parent domain object
(``Campaign``, ``MetricEntry``, ...), never a raw ``workspace_id``/
``campaign_id`` parameter — the same "no independent parameter, no
possibility of drift" pattern established throughout this codebase.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session, aliased

from app.campaigns.models import Campaign
from app.content.models import ContentBrief, ContentDistribution, ContentPiece
from app.core.ids import generate_public_id
from app.planning.models import ContentPlan
from app.measurement.models import (
    AnalysisResult,
    AnalysisResultSignal,
    DistributionMetricEvidence,
    MeasurementAnalysisRun,
    MeasurementAnalysisRunMetricEntry,
    MeasurementAnalysisRunObservationUsage,
    MeasurementAnalysisRunResult,
    MeasurementAnalysisRunSignalUsage,
    MeasurementAnalysisRunStatus,
    MeasurementObservationDerivation,
    MeasurementSignalDerivation,
    MetricEntry,
    MetricSource,
    MetricValue,
    ObservationMetricEntry,
    PerformanceObservation,
    PerformanceSignal,
    SignalObservation,
)


class MetricEntryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        campaign: Campaign,
        period_start,
        period_end,
        channel: str,
        source: MetricSource,
        client_request_id: str,
    ) -> MetricEntry:
        entry = MetricEntry(
            public_id=generate_public_id("MET"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            period_start=period_start,
            period_end=period_end,
            channel=channel,
            source=source,
            client_request_id=client_request_id,
        )
        self.session.add(entry)
        self.session.flush()
        return entry

    def get_by_workspace_and_request_id(self, *, workspace_id: uuid.UUID, client_request_id: str) -> MetricEntry | None:
        return self.session.execute(
            select(MetricEntry).where(
                MetricEntry.workspace_id == workspace_id, MetricEntry.client_request_id == client_request_id
            )
        ).scalar_one_or_none()

    def get_by_public_id(self, public_id: str) -> MetricEntry | None:
        return self.session.execute(select(MetricEntry).where(MetricEntry.public_id == public_id)).scalar_one_or_none()

    def get_by_id(self, entry_id: uuid.UUID) -> MetricEntry | None:
        return self.session.get(MetricEntry, entry_id)

    def list_for_ids(self, entry_ids: list[uuid.UUID]) -> list[MetricEntry]:
        """MVP-22: batched lookup for the Campaign Distribution Evidence
        Rollup, mirroring ``MetricValueRepository.list_for_entries``'s own
        shape — avoids an Evidence-count-dependent per-row ``get_by_id``
        loop once membership spans an entire Campaign rather than one
        Distribution."""
        if not entry_ids:
            return []
        return list(self.session.execute(select(MetricEntry).where(MetricEntry.id.in_(entry_ids))).scalars().all())

    def exists_later_in_grouping(self, entry: MetricEntry) -> bool:
        """Experiment Evidence Binding (read-time observation only, never
        stored, never a gate): true when a LATER aggregate entry exists for
        the same ``(campaign, period_start, period_end, channel)`` grouping —
        i.e. ``entry`` is not the latest row of its grouping under the same
        ``created_at DESC, id DESC`` ordering ``list_for_campaign`` uses.
        Distribution-owned entries are excluded from the comparison (they are
        isolated from the aggregate grouping, MVP-19B §37/§39)."""
        return (
            self.session.execute(
                select(MetricEntry.id)
                .where(
                    MetricEntry.campaign_id == entry.campaign_id,
                    MetricEntry.period_start == entry.period_start,
                    MetricEntry.period_end == entry.period_end,
                    MetricEntry.channel == entry.channel,
                    tuple_(MetricEntry.created_at, MetricEntry.id) > tuple_(entry.created_at, entry.id),
                    ~select(DistributionMetricEvidence.id)
                    .where(DistributionMetricEvidence.metric_entry_id == MetricEntry.id)
                    .exists(),
                )
                .limit(1)
            ).first()
            is not None
        )

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[MetricEntry]:
        """Full history, every correction preserved — ordered so the
        caller can determine "current per grouping" itself, or the
        service layer can annotate ``is_current`` (Phase 1B §M: no
        stored/mutable current pointer exists).

        MVP-19B §37/§39 (mandatory aggregate/analysis isolation): excludes
        any MetricEntry owned by a ``DistributionMetricEvidence`` row — this
        is the single query both the ``GET /metrics`` aggregate listing and
        the Measurement Analysis pipeline's ``_select_current_metric_
        entries`` share, so filtering here closes both surfaces at once,
        with no risk of the two drifting apart. A direct id/public_id/
        request_id lookup (``get_by_id``/``get_by_public_id``/
        ``get_by_workspace_and_request_id``) is deliberately NOT filtered —
        Evidence's own idempotency/detail-fetch paths need to resolve their
        own MetricEntry by id regardless of this exclusion."""
        return list(
            self.session.execute(
                select(MetricEntry)
                .where(
                    MetricEntry.campaign_id == campaign_id,
                    ~select(DistributionMetricEvidence.id)
                    .where(DistributionMetricEvidence.metric_entry_id == MetricEntry.id)
                    .exists(),
                )
                .order_by(
                    MetricEntry.period_start.asc(),
                    MetricEntry.period_end.asc(),
                    MetricEntry.channel.asc(),
                    MetricEntry.created_at.desc(),
                    MetricEntry.id.desc(),
                )
            )
            .scalars()
            .all()
        )


class MetricValueRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_many(self, *, metric_entry: MetricEntry, values: dict[str, Decimal]) -> list[MetricValue]:
        rows = [
            MetricValue(metric_entry_id=metric_entry.id, metric_name=name, value=value)
            for name, value in values.items()
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def list_for_entry(self, metric_entry_id: uuid.UUID) -> list[MetricValue]:
        return list(
            self.session.execute(
                select(MetricValue).where(MetricValue.metric_entry_id == metric_entry_id).order_by(MetricValue.metric_name.asc())
            )
            .scalars()
            .all()
        )

    def list_for_entries(self, metric_entry_ids: list[uuid.UUID]) -> list[MetricValue]:
        if not metric_entry_ids:
            return []
        return list(
            self.session.execute(select(MetricValue).where(MetricValue.metric_entry_id.in_(metric_entry_ids)))
            .scalars()
            .all()
        )


class PerformanceObservationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, campaign: Campaign, metric_name: str, value: Decimal) -> PerformanceObservation:
        row = PerformanceObservation(
            public_id=generate_public_id("OBS"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            metric_name=metric_name,
            value=value,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_public_id(self, public_id: str) -> PerformanceObservation | None:
        return self.session.execute(
            select(PerformanceObservation).where(PerformanceObservation.public_id == public_id)
        ).scalar_one_or_none()

    def get_by_id(self, observation_id: uuid.UUID) -> PerformanceObservation | None:
        return self.session.get(PerformanceObservation, observation_id)

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[PerformanceObservation]:
        return list(
            self.session.execute(
                select(PerformanceObservation)
                .where(PerformanceObservation.campaign_id == campaign_id)
                .order_by(PerformanceObservation.created_at.asc(), PerformanceObservation.id.asc())
            )
            .scalars()
            .all()
        )


class PerformanceSignalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, campaign: Campaign, summary: str) -> PerformanceSignal:
        row = PerformanceSignal(
            public_id=generate_public_id("SIG"), workspace_id=campaign.workspace_id, campaign_id=campaign.id, summary=summary
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_public_id(self, public_id: str) -> PerformanceSignal | None:
        return self.session.execute(select(PerformanceSignal).where(PerformanceSignal.public_id == public_id)).scalar_one_or_none()

    def get_by_id(self, signal_id: uuid.UUID) -> PerformanceSignal | None:
        return self.session.get(PerformanceSignal, signal_id)

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[PerformanceSignal]:
        return list(
            self.session.execute(
                select(PerformanceSignal)
                .where(PerformanceSignal.campaign_id == campaign_id)
                .order_by(PerformanceSignal.created_at.asc(), PerformanceSignal.id.asc())
            )
            .scalars()
            .all()
        )


class AnalysisResultRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, campaign: Campaign, summary: str) -> AnalysisResult:
        row = AnalysisResult(
            public_id=generate_public_id("ANL"), workspace_id=campaign.workspace_id, campaign_id=campaign.id, summary=summary
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_public_id(self, public_id: str) -> AnalysisResult | None:
        return self.session.execute(select(AnalysisResult).where(AnalysisResult.public_id == public_id)).scalar_one_or_none()

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[AnalysisResult]:
        return list(
            self.session.execute(
                select(AnalysisResult)
                .where(AnalysisResult.campaign_id == campaign_id)
                .order_by(AnalysisResult.created_at.asc(), AnalysisResult.id.asc())
            )
            .scalars()
            .all()
        )


class ObservationMetricEntryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_many(self, *, observation: PerformanceObservation, metric_entries: list[MetricEntry]) -> list[ObservationMetricEntry]:
        rows = [
            ObservationMetricEntry(workspace_id=observation.workspace_id, observation_id=observation.id, metric_entry_id=entry.id)
            for entry in metric_entries
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def list_metric_entry_ids_for_observation(self, observation_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            self.session.execute(
                select(ObservationMetricEntry.metric_entry_id).where(ObservationMetricEntry.observation_id == observation_id)
            )
            .scalars()
            .all()
        )


class SignalObservationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_many(self, *, signal: PerformanceSignal, observations: list[PerformanceObservation]) -> list[SignalObservation]:
        rows = [
            SignalObservation(workspace_id=signal.workspace_id, signal_id=signal.id, observation_id=observation.id)
            for observation in observations
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def list_observation_ids_for_signal(self, signal_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            self.session.execute(select(SignalObservation.observation_id).where(SignalObservation.signal_id == signal_id))
            .scalars()
            .all()
        )


class AnalysisResultSignalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_many(self, *, analysis_result: AnalysisResult, signals: list[PerformanceSignal]) -> list[AnalysisResultSignal]:
        rows = [
            AnalysisResultSignal(workspace_id=analysis_result.workspace_id, analysis_result_id=analysis_result.id, signal_id=signal.id)
            for signal in signals
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def list_signal_ids_for_analysis_result(self, analysis_result_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            self.session.execute(
                select(AnalysisResultSignal.signal_id).where(AnalysisResultSignal.analysis_result_id == analysis_result_id)
            )
            .scalars()
            .all()
        )


# --- Measurement Analysis Run pipeline — MVP-11B ---------------------------


class MeasurementAnalysisRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, campaign: Campaign, client_request_id: str) -> MeasurementAnalysisRun:
        run = MeasurementAnalysisRun(
            public_id=generate_public_id("MAR"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            client_request_id=client_request_id,
            status=MeasurementAnalysisRunStatus.RUNNING,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def get_by_workspace_and_request_id(
        self, *, workspace_id: uuid.UUID, client_request_id: str
    ) -> MeasurementAnalysisRun | None:
        return self.session.execute(
            select(MeasurementAnalysisRun).where(
                MeasurementAnalysisRun.workspace_id == workspace_id,
                MeasurementAnalysisRun.client_request_id == client_request_id,
            )
        ).scalar_one_or_none()

    def get_by_public_id(self, public_id: str) -> MeasurementAnalysisRun | None:
        return self.session.execute(
            select(MeasurementAnalysisRun).where(MeasurementAnalysisRun.public_id == public_id)
        ).scalar_one_or_none()

    def get_by_id(self, run_id: uuid.UUID) -> MeasurementAnalysisRun | None:
        return self.session.get(MeasurementAnalysisRun, run_id)


class MeasurementAnalysisRunMetricEntryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_many(
        self, *, run: MeasurementAnalysisRun, metric_entries: list[MetricEntry]
    ) -> list[MeasurementAnalysisRunMetricEntry]:
        rows = [
            MeasurementAnalysisRunMetricEntry(
                workspace_id=run.workspace_id, analysis_run_id=run.id, metric_entry_id=entry.id
            )
            for entry in metric_entries
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def list_metric_entry_ids_for_run(self, run_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            self.session.execute(
                select(MeasurementAnalysisRunMetricEntry.metric_entry_id).where(
                    MeasurementAnalysisRunMetricEntry.analysis_run_id == run_id
                )
            )
            .scalars()
            .all()
        )


class MeasurementObservationDerivationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_identity(
        self, *, source_metric_entry_id: uuid.UUID, metric_name: str
    ) -> MeasurementObservationDerivation | None:
        return self.session.execute(
            select(MeasurementObservationDerivation).where(
                MeasurementObservationDerivation.source_metric_entry_id == source_metric_entry_id,
                MeasurementObservationDerivation.metric_name == metric_name,
            )
        ).scalar_one_or_none()

    def create(
        self,
        *,
        observation: PerformanceObservation,
        source_metric_entry_id: uuid.UUID,
        metric_name: str,
        creator_run: MeasurementAnalysisRun,
    ) -> MeasurementObservationDerivation:
        row = MeasurementObservationDerivation(
            workspace_id=observation.workspace_id,
            observation_id=observation.id,
            source_metric_entry_id=source_metric_entry_id,
            metric_name=metric_name,
            creator_analysis_run_id=creator_run.id,
        )
        self.session.add(row)
        self.session.flush()
        return row


class MeasurementSignalDerivationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_identity(
        self, *, current_observation_id: uuid.UUID, prior_observation_id: uuid.UUID
    ) -> MeasurementSignalDerivation | None:
        return self.session.execute(
            select(MeasurementSignalDerivation).where(
                MeasurementSignalDerivation.current_observation_id == current_observation_id,
                MeasurementSignalDerivation.prior_observation_id == prior_observation_id,
            )
        ).scalar_one_or_none()

    def create(
        self,
        *,
        signal: PerformanceSignal,
        current_observation_id: uuid.UUID,
        prior_observation_id: uuid.UUID,
        creator_run: MeasurementAnalysisRun,
    ) -> MeasurementSignalDerivation:
        row = MeasurementSignalDerivation(
            workspace_id=signal.workspace_id,
            signal_id=signal.id,
            current_observation_id=current_observation_id,
            prior_observation_id=prior_observation_id,
            creator_analysis_run_id=creator_run.id,
        )
        self.session.add(row)
        self.session.flush()
        return row


class MeasurementAnalysisRunObservationUsageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self, *, run: MeasurementAnalysisRun, observation: PerformanceObservation
    ) -> MeasurementAnalysisRunObservationUsage:
        row = MeasurementAnalysisRunObservationUsage(
            workspace_id=run.workspace_id, analysis_run_id=run.id, observation_id=observation.id
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_observation_ids_for_run(self, run_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            self.session.execute(
                select(MeasurementAnalysisRunObservationUsage.observation_id).where(
                    MeasurementAnalysisRunObservationUsage.analysis_run_id == run_id
                )
            )
            .scalars()
            .all()
        )


class MeasurementAnalysisRunSignalUsageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, run: MeasurementAnalysisRun, signal: PerformanceSignal) -> MeasurementAnalysisRunSignalUsage:
        row = MeasurementAnalysisRunSignalUsage(
            workspace_id=run.workspace_id, analysis_run_id=run.id, signal_id=signal.id
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_signal_ids_for_run(self, run_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            self.session.execute(
                select(MeasurementAnalysisRunSignalUsage.signal_id).where(
                    MeasurementAnalysisRunSignalUsage.analysis_run_id == run_id
                )
            )
            .scalars()
            .all()
        )


class MeasurementAnalysisRunResultRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self, *, run: MeasurementAnalysisRun, analysis_result: AnalysisResult
    ) -> MeasurementAnalysisRunResult:
        row = MeasurementAnalysisRunResult(
            workspace_id=run.workspace_id, analysis_run_id=run.id, analysis_result_id=analysis_result.id
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_for_run(self, run_id: uuid.UUID) -> MeasurementAnalysisRunResult | None:
        return self.session.execute(
            select(MeasurementAnalysisRunResult).where(MeasurementAnalysisRunResult.analysis_run_id == run_id)
        ).scalar_one_or_none()


# --- Distribution-linked Measurement Evidence — MVP-19B --------------------


class DistributionMetricEvidenceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        distribution: ContentDistribution,
        metric_entry: MetricEntry,
        created_by_user_id: uuid.UUID,
        source_reference: str | None = None,
        supersedes: DistributionMetricEvidence | None = None,
        correction_reason: str | None = None,
    ) -> DistributionMetricEvidence:
        row = DistributionMetricEvidence(
            public_id=generate_public_id("DME"),
            workspace_id=distribution.workspace_id,
            distribution_id=distribution.id,
            metric_entry_id=metric_entry.id,
            supersedes_evidence_id=supersedes.id if supersedes is not None else None,
            created_by_user_id=created_by_user_id,
            source_reference=source_reference,
            correction_reason=correction_reason,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_public_id(self, public_id: str, *, for_update: bool = False) -> DistributionMetricEvidence | None:
        query = select(DistributionMetricEvidence).where(DistributionMetricEvidence.public_id == public_id)
        if for_update:
            query = query.with_for_update()
        return self.session.execute(query).scalar_one_or_none()

    def get_by_id(self, evidence_id: uuid.UUID) -> DistributionMetricEvidence | None:
        return self.session.get(DistributionMetricEvidence, evidence_id)

    def get_by_metric_entry_id(self, metric_entry_id: uuid.UUID) -> DistributionMetricEvidence | None:
        return self.session.execute(
            select(DistributionMetricEvidence).where(DistributionMetricEvidence.metric_entry_id == metric_entry_id)
        ).scalar_one_or_none()

    def get_successor(self, evidence_id: uuid.UUID) -> DistributionMetricEvidence | None:
        """MVP-19B §56: "current" (no successor yet) is always derived at
        read time from the ``UNIQUE(supersedes_evidence_id)`` relationship
        itself, never a stored flag."""
        return self.session.execute(
            select(DistributionMetricEvidence).where(DistributionMetricEvidence.supersedes_evidence_id == evidence_id)
        ).scalar_one_or_none()

    def list_superseded_ids_for_distribution(self, distribution_id: uuid.UUID) -> set[uuid.UUID]:
        """Every Evidence id, within this Distribution, that some other row
        already supersedes — used to derive ``is_current`` for every row on
        a listing page without an N+1 query per row."""
        return set(
            self.session.execute(
                select(DistributionMetricEvidence.supersedes_evidence_id).where(
                    DistributionMetricEvidence.distribution_id == distribution_id,
                    DistributionMetricEvidence.supersedes_evidence_id.is_not(None),
                )
            )
            .scalars()
            .all()
        )

    def list_current_for_distribution(self, distribution_id: uuid.UUID) -> list[DistributionMetricEvidence]:
        """MVP-21/MVP-21A-R1 §E/§F: the sole authoritative current-Evidence
        primitive for the Distribution Evidence Summary — a single SELECT
        with a correlated ``NOT EXISTS`` anti-join (the same shape as
        ``MetricEntryRepository.list_for_campaign``'s own anti-join, applied
        here to ``supersedes_evidence_id`` instead), so the entire
        current-membership decision is resolved within one statement
        snapshot. Unlike ``list_superseded_ids_for_distribution`` + a
        separate item fetch, this cannot observe a hybrid state where a
        concurrent correction's predecessor is excluded but its successor
        was never fetched (MVP-21A-R1 §D). Unpaginated by design — the
        summary must cover every current row, never one page."""
        successor = aliased(DistributionMetricEvidence)
        return list(
            self.session.execute(
                select(DistributionMetricEvidence)
                .where(
                    DistributionMetricEvidence.distribution_id == distribution_id,
                    ~select(successor.id).where(successor.supersedes_evidence_id == DistributionMetricEvidence.id).exists(),
                )
            )
            .scalars()
            .all()
        )

    def list_current_for_campaign(
        self, campaign_id: uuid.UUID
    ) -> list[tuple[DistributionMetricEvidence, ContentDistribution, ContentPiece]]:
        """MVP-22/MVP-22A §I: the sole authoritative Campaign-wide
        current-Evidence primitive for the Campaign Distribution Evidence
        Rollup — one SELECT, joined through the exact tenant-safe
        traversal ``ContentPieceRepository.list_for_campaign`` already
        established (``ContentPiece -> ContentBrief -> ContentPlan ->
        Campaign``), with the identical correlated ``NOT EXISTS`` anti-join
        ``list_current_for_distribution`` already uses. Selecting
        ``ContentDistribution``/``ContentPiece`` alongside the Evidence row
        in the same statement gives full provenance (public id, channel,
        frozen content_version_id) at zero extra query cost — no
        per-Distribution loop, no per-row provenance query, one statement
        snapshot spanning every eligible Distribution in the Campaign."""
        successor = aliased(DistributionMetricEvidence)
        return list(
            self.session.execute(
                select(DistributionMetricEvidence, ContentDistribution, ContentPiece)
                .join(ContentDistribution, ContentDistribution.id == DistributionMetricEvidence.distribution_id)
                .join(ContentPiece, ContentPiece.id == ContentDistribution.content_piece_id)
                .join(ContentBrief, ContentBrief.id == ContentPiece.content_brief_id)
                .join(ContentPlan, ContentPlan.id == ContentBrief.content_plan_id)
                .where(
                    ContentPlan.campaign_id == campaign_id,
                    ~select(successor.id)
                    .where(successor.supersedes_evidence_id == DistributionMetricEvidence.id)
                    .exists(),
                )
            )
            .all()
        )

    def list_for_distribution(
        self, *, distribution_id: uuid.UUID, limit: int, offset: int
    ) -> tuple[list[DistributionMetricEvidence], int]:
        total = self.session.execute(
            select(func.count())
            .select_from(DistributionMetricEvidence)
            .where(DistributionMetricEvidence.distribution_id == distribution_id)
        ).scalar_one()
        items = list(
            self.session.execute(
                select(DistributionMetricEvidence)
                .where(DistributionMetricEvidence.distribution_id == distribution_id)
                .order_by(DistributionMetricEvidence.created_at.desc(), DistributionMetricEvidence.id.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        return items, total
