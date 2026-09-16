"""Public DTOs for the Content read surface. Never expose an internal
UUID, a raw ``workspace_id``, or an agent identifier — only public_id-
derived fields (mirroring ``app/strategy/schemas.py``/
``app/planning/schemas.py``).

No field here ever represents approval authority, production
authorization, distribution readiness, or a chain-of-thought/reasoning
trace. ``created_by_user_id``/``reviewer_user_id`` are deliberately not
exposed on these read models — actor attribution lives in the Audit Event
trail, not the domain read model itself, matching every prior stage's own
precedent (Strategy/Research/Planning expose none of their own actor
fields either).

``ContentApprovalPublic`` (MVP-17B): frozen at MVP-17A/§K — exactly
``id``/``status``/``decided_at``, nothing more. No full approval history is
exposed; ``ContentPieceDetailResponse.latest_approval`` is sufficient
because MVP-17A-R1's concurrency repair guarantees at most one OPEN
Approval can ever exist per Version.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.content.models import ContentApproval, ContentApprovalStatus, ContentDistribution, ContentDistributionStatus, ContentPiece, ContentPieceStatus, ContentVersion


class ContentPiecePublic(BaseModel):
    id: str
    format: str
    objective: str
    funnel_stage: str
    cta: str
    channel: str
    status: ContentPieceStatus
    archived_at: datetime | None
    created_at: datetime


class ContentVersionPublic(BaseModel):
    id: str
    payload: dict[str, Any]
    created_at: datetime


class ContentApprovalPublic(BaseModel):
    id: str
    status: ContentApprovalStatus
    decided_at: datetime | None


class ContentPieceListResponse(BaseModel):
    items: list[ContentPiecePublic]


class DistributionPublic(BaseModel):
    id: str
    status: ContentDistributionStatus
    channel: str
    external_reference: str | None
    ready_at: datetime
    distributed_at: datetime | None
    # MVP-24: identity-only (MVP-24A-R1) — public IDs of the
    # TrackingRequirements declared applicable to this Distribution.
    # Never a copy of TrackingRequirement.name/status — those remain
    # current-state-only, reachable only via a fresh GET /tracking.
    tracking_requirement_ids: list[str] = Field(default_factory=list)


class ContentPieceDetailResponse(BaseModel):
    piece: ContentPiecePublic
    latest_version: ContentVersionPublic | None
    latest_approval: ContentApprovalPublic | None
    distribution: DistributionPublic | None


class RecordDistributedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_reference: str | None = Field(default=None, max_length=2048)


class TrackingRequirementAssociationRequest(BaseModel):
    """MVP-24: the sole writable field for associating/dissociating a
    TrackingRequirement with a ContentDistribution — no client-supplied
    campaign_id/workspace_id/actor; those are derived from the URL/session,
    matching every other write schema in this codebase."""

    model_config = ConfigDict(extra="forbid")
    tracking_requirement_id: str = Field(min_length=1, max_length=20)


class RecordApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Only the three authorized terminal decisions (MVP-17B §24) —
    # EXPIRED/REQUESTED/UNDER_REVIEW are never accepted from a client.
    decision: Literal[
        ContentApprovalStatus.APPROVED, ContentApprovalStatus.CHANGES_REQUESTED, ContentApprovalStatus.REJECTED
    ]


class CreateContentVersionRequest(BaseModel):
    """MVP-20: the client supplies only the immutable version content.
    Server derives workspace/piece linkage (from the authorized URL),
    public_id, created_at, created_by_user_id, and the coupled Piece
    status transition — none of those are client-controlled."""

    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any]


def content_piece_to_public(piece: ContentPiece) -> ContentPiecePublic:
    return ContentPiecePublic(
        id=piece.public_id,
        format=piece.format,
        objective=piece.objective,
        funnel_stage=piece.funnel_stage,
        cta=piece.cta,
        channel=piece.channel,
        status=piece.status,
        archived_at=piece.archived_at,
        created_at=piece.created_at,
    )


def content_version_to_public(version: ContentVersion) -> ContentVersionPublic:
    return ContentVersionPublic(id=version.public_id, payload=version.payload, created_at=version.created_at)


def content_approval_to_public(approval: ContentApproval) -> ContentApprovalPublic:
    return ContentApprovalPublic(id=approval.public_id, status=approval.status, decided_at=approval.decided_at)


def distribution_to_public(
    distribution: ContentDistribution, *, tracking_requirement_ids: list[str] | None = None
) -> DistributionPublic:
    return DistributionPublic(
        id=distribution.public_id, status=distribution.status, channel=distribution.channel,
        external_reference=distribution.external_reference, ready_at=distribution.ready_at,
        distributed_at=distribution.distributed_at,
        tracking_requirement_ids=tracking_requirement_ids if tracking_requirement_ids is not None else [],
    )
