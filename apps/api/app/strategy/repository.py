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
from app.strategy.models import Experiment, Hypothesis, HypothesisStatus, Positioning, Strategy


class StrategyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        campaign: Campaign,
        campaign_run: CampaignRun,
        stage_execution: RunStageExecution,
        version: int,
        summary: str,
    ) -> Strategy:
        strategy = Strategy(
            public_id=generate_public_id("STR"),
            workspace_id=campaign.workspace_id,
            campaign_id=campaign.id,
            campaign_run_id=campaign_run.id,
            stage_execution_id=stage_execution.id,
            version=version,
            summary=summary,
        )
        self.session.add(strategy)
        self.session.flush()
        return strategy

    def get_by_public_id(self, public_id: str) -> Strategy | None:
        return self.session.execute(select(Strategy).where(Strategy.public_id == public_id)).scalar_one_or_none()

    def get_current_for_campaign(self, campaign_id: uuid.UUID) -> Strategy | None:
        return self.session.execute(
            select(Strategy).where(Strategy.campaign_id == campaign_id).order_by(Strategy.version.desc()).limit(1)
        ).scalar_one_or_none()

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
