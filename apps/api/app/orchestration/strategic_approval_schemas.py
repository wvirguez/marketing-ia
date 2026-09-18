"""Public DTOs for the StrategicApproval read/write surface — MVP-29B
(frozen MVP-29A contract). Never expose an internal UUID, a raw
``workspace_id``, or a raw campaign UUID — only public_id-derived fields
and cross-references, mirroring
``app/orchestration/strategic_decision_schemas.py`` exactly.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.orchestration.models import StrategicApproval, StrategicApprovalOutcome


class StrategicApprovalPublic(BaseModel):
    """MVP-29A §K: strictly insert-only — there is no ``current``/
    ``superseded_at`` pair here at all, unlike ``StrategicDecisionPublic``.
    ``strategic_decision_id`` is never nullable — every Approval row
    requires an already-resolved Decision (MVP-29A §D/§E)."""

    id: str
    campaign_id: str
    strategic_decision_id: str
    outcome: StrategicApprovalOutcome
    created_at: datetime


class StrategicApprovalListResponse(BaseModel):
    items: list[StrategicApprovalPublic]


class RecordStrategicApprovalRequest(BaseModel):
    """The sole writable field for a new StrategicApproval (MVP-29A §U) —
    no client-supplied ``strategic_decision_id`` (already in the URL path,
    resolved campaign-scoped server-side, MVP-29B §8) and no rationale
    field (MVP-29A §5/MVP-29B §5: not invented as a required field)."""

    model_config = ConfigDict(extra="forbid")

    outcome: StrategicApprovalOutcome


def strategic_approval_to_public(
    approval: StrategicApproval,
    *,
    campaign_public_id: str,
    decision_public_id: str,
) -> StrategicApprovalPublic:
    return StrategicApprovalPublic(
        id=approval.public_id,
        campaign_id=campaign_public_id,
        strategic_decision_id=decision_public_id,
        outcome=approval.outcome,
        created_at=approval.created_at,
    )
