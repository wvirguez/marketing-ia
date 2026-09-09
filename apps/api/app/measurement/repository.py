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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign
from app.core.ids import generate_public_id
from app.measurement.models import (
    AnalysisResult,
    AnalysisResultSignal,
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

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[MetricEntry]:
        """Full history, every correction preserved — ordered so the
        caller can determine "current per grouping" itself, or the
        service layer can annotate ``is_current`` (Phase 1B §M: no
        stored/mutable current pointer exists)."""
        return list(
            self.session.execute(
                select(MetricEntry)
                .where(MetricEntry.campaign_id == campaign_id)
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
