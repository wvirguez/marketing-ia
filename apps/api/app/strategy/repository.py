"""Data access for Strategy / Positioning / Hypothesis / Experiment. No
repository here calls ``session.commit()`` — see
``app/persistence/session.py`` and ``app/strategy/service.py`` for the
transaction-ownership boundary.

Every ``create``/``create_many`` method takes the parent domain object
(``Campaign``, ``CampaignRun``, ``RunStageExecution``, ``Strategy``,
``Hypothesis``), never a raw ``workspace_id``/``campaign_id``/
``strategy_id``/``hypothesis_id`` parameter — the same "no independent
parameter, no possibility of drift" pattern established in
``app/campaigns/repository.py``/``app/research/repository.py``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign, CampaignRun
from app.core.ids import generate_public_id
from app.orchestration.models import RunStageExecution
from app.strategy.models import Experiment, Hypothesis, HypothesisStatus, Positioning, Strategy, StrategyOrigin


class StrategyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        campaign: Campaign,
        version: int,
        summary: str,
        origin: StrategyOrigin,
        campaign_run: CampaignRun | None = None,
        stage_execution: RunStageExecution | None = None,
    ) -> Strategy:
        """``campaign_run``/``stage_execution`` are required together for
        ``origin=BOOTSTRAP`` and must be omitted together for
        ``origin=REVISION`` (MVP-30A-R1) — the DB's own
        ``ck_strategies_origin_bootstrap_fields`` CHECK constraint is the
        actual backstop, never trusted as merely an application-level
        convention (mirrors every other origin/status invariant in this
        codebase)."""
        strategy = Strategy(
            public_id=generate_public_id("STR"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            origin=origin,
            campaign_run_id=campaign_run.id if campaign_run is not None else None,
            stage_execution_id=stage_execution.id if stage_execution is not None else None,
            version=version,
            summary=summary,
        )
        self.session.add(strategy)
        self.session.flush()
        return strategy

    def get_by_public_id(self, public_id: str) -> Strategy | None:
        return self.session.execute(select(Strategy).where(Strategy.public_id == public_id)).scalar_one_or_none()

    def get_for_campaign_by_public_id(
        self, *, campaign_id: uuid.UUID, public_id: str, for_update: bool = False
    ) -> Strategy | None:
        """Non-leaky, campaign-scoped resource lookup — identical
        discipline to every other bounded context's own
        ``get_for_campaign_by_public_id`` (e.g.
        ``app/orchestration/repository.py::StrategicDecisionRepository``).
        Used by ``StrategyRevisionService`` to resolve the exact base
        Strategy a governed Revision targets (MVP-30B) — never a global
        lookup trusting a client-supplied campaign match."""
        query = select(Strategy).where(Strategy.campaign_id == campaign_id, Strategy.public_id == public_id)
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.execute(query).scalar_one_or_none()

    def get_current_for_campaign(self, campaign_id: uuid.UUID) -> Strategy | None:
        return self.session.execute(
            select(Strategy).where(Strategy.campaign_id == campaign_id).order_by(Strategy.version.desc()).limit(1)
        ).scalar_one_or_none()

    def list_for_campaign(self, campaign_id: uuid.UUID) -> list[Strategy]:
        return list(
            self.session.execute(
                select(Strategy).where(Strategy.campaign_id == campaign_id).order_by(Strategy.version.asc())
            )
            .scalars()
            .all()
        )

    def next_version_for_campaign(self, campaign_id: uuid.UUID) -> int:
        current = self.session.execute(
            select(func.max(Strategy.version)).where(Strategy.campaign_id == campaign_id)
        ).scalar_one()
        return (current or 0) + 1


class PositioningRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, strategy: Strategy, statement: str) -> Positioning:
        positioning = Positioning(
            public_id=generate_public_id("POS"),
            strategy_id=strategy.id,
            statement=statement,
        )
        self.session.add(positioning)
        self.session.flush()
        return positioning

    def get_for_strategy(self, strategy_id: uuid.UUID) -> Positioning | None:
        return self.session.execute(
            select(Positioning).where(Positioning.strategy_id == strategy_id)
        ).scalar_one_or_none()


class HypothesisRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, strategy: Strategy, statement: str) -> Hypothesis:
        """MVP-31B: the single-item governed counterpart to
        ``create_many`` below — used only by
        ``StrategyService.create_hypothesis`` (the new governed,
        human-reachable path). ``create_many`` remains unchanged and
        bootstrap-only (MVP-31A-R1: no ``origin`` discriminator
        distinguishes the two — a governed Hypothesis is the exact same
        domain entity as a bootstrap one, differing only in which code
        path inserted it, which is not a canonical stored property)."""
        hypothesis = Hypothesis(
            public_id=generate_public_id("HYP"),
            workspace_id=strategy.workspace_id,
            strategy_id=strategy.id,
            statement=statement,
            status=HypothesisStatus.OPEN,
        )
        self.session.add(hypothesis)
        self.session.flush()
        return hypothesis

    def create_many(self, *, strategy: Strategy, items: list[dict]) -> list[Hypothesis]:
        rows = [
            Hypothesis(
                public_id=generate_public_id("HYP"),
                workspace_id=strategy.workspace_id,
                strategy_id=strategy.id,
                statement=item["statement"],
                status=HypothesisStatus.OPEN,
            )
            for item in items
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def get_by_public_id(self, public_id: str, *, for_update: bool = False) -> Hypothesis | None:
        query = select(Hypothesis).where(Hypothesis.public_id == public_id)
        if for_update:
            query = query.with_for_update()
        return self.session.execute(query).scalar_one_or_none()

    def list_for_strategy(self, strategy_id: uuid.UUID) -> list[Hypothesis]:
        return list(
            self.session.execute(
                select(Hypothesis)
                .where(Hypothesis.strategy_id == strategy_id)
                .order_by(Hypothesis.created_at.asc(), Hypothesis.id.asc())
            )
            .scalars()
            .all()
        )


class ExperimentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_many(self, *, hypothesis: Hypothesis, items: list[dict]) -> list[Experiment]:
        rows = [
            Experiment(
                public_id=generate_public_id("EXP"),
                workspace_id=hypothesis.workspace_id,
                hypothesis_id=hypothesis.id,
                description=item["description"],
            )
            for item in items
        ]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def list_for_hypothesis(self, hypothesis_id: uuid.UUID) -> list[Experiment]:
        return list(
            self.session.execute(
                select(Experiment)
                .where(Experiment.hypothesis_id == hypothesis_id)
                .order_by(Experiment.created_at.asc(), Experiment.id.asc())
            )
            .scalars()
            .all()
        )
