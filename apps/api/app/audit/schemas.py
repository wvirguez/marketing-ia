"""Public DTO for Audit Event. Never exposes an internal UUID — only
public_id-derived fields, matching every other module."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.audit.models import AuditEvent


class AuditEventPublic(BaseModel):
    id: str
    event_type: str
    previous_state: str | None
    new_state: str | None
    actor_type: str
    actor_user_id: str | None
    decision_id: str | None
    request_id: str | None
    created_at: datetime


class AuditEventListResponse(BaseModel):
    items: list[AuditEventPublic]
    limit: int
    offset: int
    total: int


def audit_event_to_public(
    event: AuditEvent, *, actor_public_id: str | None, decision_public_id: str | None = None
) -> AuditEventPublic:
    return AuditEventPublic(
        id=event.public_id,
        event_type=event.event_type,
        previous_state=event.previous_state,
        new_state=event.new_state,
        actor_type=event.actor_type.value,
        actor_user_id=actor_public_id,
        decision_id=decision_public_id,
        request_id=event.request_id,
        created_at=event.created_at,
    )
