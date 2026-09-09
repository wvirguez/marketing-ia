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
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.content.models import ContentPiece, ContentPieceStatus, ContentVersion


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


class ContentPieceListResponse(BaseModel):
    items: list[ContentPiecePublic]


class ContentPieceDetailResponse(BaseModel):
    piece: ContentPiecePublic
    latest_version: ContentVersionPublic | None


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
