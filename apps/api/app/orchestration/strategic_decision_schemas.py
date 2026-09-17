"""Public DTOs for the StrategicDecision read/write surface — MVP-28B
(frozen MVP-28A/-R1/-R2 contract). Never expose an internal UUID, a raw
``workspace_id``, or a raw campaign UUID — only public_id-derived fields
and cross-references, mirroring ``app/commercial/schemas.py``'s own
convention exactly.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.orchestration.models import StrategicDecision, StrategicDecisionType


class StrategicDecisionPublic(BaseModel):
    """MVP-28A-R2 §H/§J: ``current`` is derived (``superseded_at IS NULL``),
    never a second persisted status column — the same discipline
    ``CommercialObjectivePublic``/``OfferPublic`` already establish.
    ``strategic_recommendation_candidate_id`` is nullable in the response
    only because it is nullable at the schema level (Model C, reserved for
    a future alternate origin) — every row created through this MVP's own
    write path always has one."""

    id: str
    campaign_id: str
    strategic_recommendation_candidate_id: str | None
    decision_type: StrategicDecisionType
    statement: str
    created_at: datetime
    current: bool
    superseded_at: datetime | None
    superseded_by_strategic_decision_id: str | None


class StrategicDecisionListResponse(BaseModel):
    items: list[StrategicDecisionPublic]


class RecordStrategicDecisionRequest(BaseModel):
    """The writable fields for a new StrategicDecision (MVP-28A-R1 §E,
    Model C) — no client-supplied ``current``/superseded-* linkage; the
    Recommendation is resolved, campaign-scoped, server-side before its
    internal id is ever used (never a raw client-supplied UUID trusted
    directly, MVP-28A-R2 §6)."""

    model_config = ConfigDict(extra="forbid")

    strategic_recommendation_candidate_id: str = Field(min_length=1)
    decision_type: StrategicDecisionType
    statement: str = Field(min_length=1, max_length=4000)


class SupersedeStrategicDecisionRequest(BaseModel):
    """Supersession never accepts a Recommendation id from the client
    (MVP-28A-R2 §8/§12: a replacement always shares the *original's own*
    ``strategic_recommendation_candidate_id`` — never a client-supplied
    value) — this is also what makes cross-Recommendation/cross-Campaign/
    cross-Workspace replacement structurally impossible, the same
    discipline ``app/commercial/service.py``'s own supersession already
    establishes."""

    model_config = ConfigDict(extra="forbid")

    decision_type: StrategicDecisionType
    statement: str = Field(min_length=1, max_length=4000)


def strategic_decision_to_public(
    decision: StrategicDecision,
    *,
    campaign_public_id: str,
    recommendation_public_id: str | None,
    superseded_by_public_id: str | None = None,
) -> StrategicDecisionPublic:
    return StrategicDecisionPublic(
        id=decision.public_id,
        campaign_id=campaign_public_id,
        strategic_recommendation_candidate_id=recommendation_public_id,
        decision_type=decision.decision_type,
        statement=decision.statement,
        created_at=decision.created_at,
        current=decision.superseded_at is None,
        superseded_at=decision.superseded_at,
        superseded_by_strategic_decision_id=superseded_by_public_id,
    )
