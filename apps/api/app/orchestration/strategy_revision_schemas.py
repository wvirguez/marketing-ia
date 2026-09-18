"""Public DTOs for the Governed Strategy Revision surface — MVP-30B
(frozen MVP-30A/-30A-R1 contract). Never expose an internal UUID, a raw
``workspace_id``, or a raw campaign UUID — only public_id-derived fields
and cross-references, mirroring
``app/orchestration/strategic_approval_schemas.py`` exactly.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.orchestration.models import StrategyRevision
from app.strategy.schemas import PositioningPublic, StrategyPublic


class StrategyRevisionPublic(BaseModel):
    """The durable governance-provenance record itself (MVP-30A-R1 §M) —
    never derived solely from AuditEvent."""

    id: str
    campaign_id: str
    strategic_approval_id: str
    base_strategy_id: str
    result_strategy_id: str
    created_at: datetime


class RecordStrategyRevisionRequest(BaseModel):
    """The minimum client assertions for a governed Revision (MVP-30A-R1
    §29/MVP-30B §17/§38): the eligible Approval is named explicitly by its
    own public_id — never heuristically selected (latest/MAX/first row) —
    and its Decision is always derived server-side from that Approval's own
    FK, never supplied by the client at all. ``summary``/
    ``positioning_statement`` are the complete new state (MVP-30A-R1 §Q) —
    no partial patch, no inherited/omitted fields. No ``version``,
    internal UUID, ``workspace_id``, ``campaign_run_id``,
    ``stage_execution_id``, ``origin``, or ``result_strategy_id`` is ever
    accepted from the client — all server-derived."""

    model_config = ConfigDict(extra="forbid")

    strategic_approval_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    positioning_statement: str = Field(min_length=1)


class StrategyRevisionResult(BaseModel):
    strategy: StrategyPublic
    positioning: PositioningPublic
    revision: StrategyRevisionPublic


class StrategyHistoryItem(BaseModel):
    strategy: StrategyPublic
    # None for BOOTSTRAP-origin (legacy) Strategy versions — never
    # fabricated (MVP-30A-R1 §AG: no synthetic historical governance
    # records).
    revision: StrategyRevisionPublic | None


class StrategyHistoryResponse(BaseModel):
    items: list[StrategyHistoryItem]


def strategy_revision_to_public(
    revision: StrategyRevision,
    *,
    campaign_public_id: str,
    strategic_approval_public_id: str,
    base_strategy_public_id: str,
    result_strategy_public_id: str,
) -> StrategyRevisionPublic:
    return StrategyRevisionPublic(
        id=revision.public_id,
        campaign_id=campaign_public_id,
        strategic_approval_id=strategic_approval_public_id,
        base_strategy_id=base_strategy_public_id,
        result_strategy_id=result_strategy_public_id,
        created_at=revision.created_at,
    )
