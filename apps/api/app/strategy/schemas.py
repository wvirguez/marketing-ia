"""Public DTOs for the strategy read surface. Never expose an internal
UUID or a raw ``workspace_id`` — only public_id-derived fields (BACKEND-08
§19, mirroring ``app/research/schemas.py``).

No field here ever represents approval, maturity, a Strategic Decision, or
a chain-of-thought/reasoning trace — see ``app/strategy/models.py`` for why
none of those exist on the underlying models in the first place.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.strategy.models import Experiment, Hypothesis, Positioning, Strategy


class PositioningPublic(BaseModel):
    id: str
    statement: str
    created_at: datetime


class HypothesisPublic(BaseModel):
    id: str
    statement: str
    status: str
    created_at: datetime


class ExperimentPublic(BaseModel):
    id: str
    hypothesis_id: str
    description: str
    status: str | None
    created_at: datetime


class StrategyPublic(BaseModel):
    id: str
    campaign_id: str
    version: int
    summary: str
    created_at: datetime


class StrategyOutputResponse(BaseModel):
    strategy: StrategyPublic | None
    positioning: PositioningPublic | None
    hypotheses: list[HypothesisPublic]
    experiments: list[ExperimentPublic]


def strategy_to_public(strategy: Strategy, *, campaign_public_id: str) -> StrategyPublic:
    return StrategyPublic(
        id=strategy.public_id,
        campaign_id=campaign_public_id,
        version=strategy.version,
        summary=strategy.summary,
        created_at=strategy.created_at,
    )


def positioning_to_public(positioning: Positioning) -> PositioningPublic:
    return PositioningPublic(
        id=positioning.public_id,
        statement=positioning.statement,
        created_at=positioning.created_at,
    )


def hypothesis_to_public(hypothesis: Hypothesis) -> HypothesisPublic:
    return HypothesisPublic(
        id=hypothesis.public_id,
        statement=hypothesis.statement,
        status=hypothesis.status.value,
        created_at=hypothesis.created_at,
    )


def experiment_to_public(experiment: Experiment, *, hypothesis_public_id: str) -> ExperimentPublic:
    return ExperimentPublic(
        id=experiment.public_id,
        hypothesis_id=hypothesis_public_id,
        description=experiment.description,
        status=experiment.status,
        created_at=experiment.created_at,
    )
