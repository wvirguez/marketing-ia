"""Data access for Audit Event — append-only by construction: this
module deliberately exposes no update/delete method at all (BACKEND-06
§21). No repository here calls ``session.commit()`` — see
``app/persistence/session.py``; the caller (an orchestration service
operation) commits the event in the same transaction as the state
change it records, so the two can never diverge (BACKEND-06 §28).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import ActorType, AuditEvent
from app.core.ids import generate_public_id


class AuditEventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        workspace_id: uuid.UUID,
        event_type: str,
        actor_type: ActorType,
        campaign_id: uuid.UUID | None = None,
        campaign_run_id: uuid.UUID | None = None,
        stage_execution_id: uuid.UUID | None = None,
        decision_request_id: uuid.UUID | None = None,
        previous_state: str | None = None,
        new_state: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            public_id=generate_public_id("AUDT"),
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            campaign_run_id=campaign_run_id,
            stage_execution_id=stage_execution_id,
            decision_request_id=decision_request_id,
            event_type=event_type,
            previous_state=previous_state,
            new_state=new_state,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def list_for_run(self, *, campaign_run_id: uuid.UUID, limit: int, offset: int) -> tuple[list[AuditEvent], int]:
        total = self.session.execute(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.campaign_run_id == campaign_run_id)
        ).scalar_one()

        items = (
            self.session.execute(
                select(AuditEvent)
                .where(AuditEvent.campaign_run_id == campaign_run_id)
                .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        return list(items), total
