"""Audit Event — BACKEND-06, implementing the entity already named and
prefixed by BACKEND-01 (`docs/backend/BACKEND-01-ARCHITECTURE.md` §8,
`docs/backend/BACKEND-01-DOMAIN-MODEL.md` §3: prefix ``AUDT``). Kept as
its own top-level module per the proposed module boundary (§9 of the
architecture doc lists ``audit/`` separately from ``orchestration/``),
so a future module beyond orchestration (content, paid_media, ...) can
append to the same event stream without importing ``orchestration``.

Append-only, by construction, not just by convention: this model
deliberately does NOT use ``TimestampMixin`` — there is no ``updated_at``
column at all, so there is no ``onupdate`` trigger a future change could
ever accidentally rely on. CURRENT STATE != STATE TRANSITION HISTORY
(BACKEND-06 §21): the event log is evidence, not mutable operational
state, and no router in this codebase exposes ``PATCH``/``DELETE`` for
it (see ``app/audit/repository.py`` — no update/delete method exists
either).

AUDIT EVENT != GOVERNANCE DECISION (BACKEND-01 §8): recording an event
here never itself grants authority over anything. A Human Decision
Response is the authority-bearing record; an Audit Event referencing it
is only a fact log entry about it having happened.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, UUIDPrimaryKeyMixin


class ActorType(str, enum.Enum):
    """BACKEND-06 §22. ``AGENT`` exists only for forward compatibility —
    no BACKEND-06 code path ever emits an event with this actor type,
    since no agent executes anything yet."""

    USER = "USER"
    SYSTEM = "SYSTEM"
    AGENT = "AGENT"


class AuditEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_events"

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaigns.id"), default=None, index=True)
    campaign_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaign_runs.id"), default=None, index=True
    )
    stage_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("run_stage_executions.id"), default=None, index=True
    )
    # BACKEND-06R: without this, a decision-related event
    # (event_type="orchestration.decision.opened"/"...resolved") could
    # not be attributed to a specific HumanDecisionRequest — a run may
    # legitimately raise more than one decision, sequentially, over its
    # lifetime, and the events for two such requests would otherwise be
    # indistinguishable except by assuming strict chronological pairing
    # (inference), which the append-only traceability invariant
    # forbids. Every other entity this table can reference
    # (campaign/run/stage) already had its own FK; this was the one gap.
    decision_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("human_decision_requests.id"), default=None, index=True
    )
    # BACKEND-07 §13: same reasoning as decision_request_id above — a
    # research.report.recorded/audience.profile.recorded event must
    # identify its exact ResearchReport/AudienceProfile, never only be
    # inferable from campaign_id/campaign_run_id/stage_execution_id
    # (which two different report versions for the same campaign/run
    # would share).
    research_report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_reports.id"), default=None, index=True
    )
    audience_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audience_profiles.id"), default=None, index=True
    )
    # A short, stable, dot-separated tag (e.g. "orchestration.run.transitioned")
    # — see app/orchestration/service.py for the fixed set BACKEND-06 emits.
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    previous_state: Mapped[str | None] = mapped_column(String(100), default=None)
    new_state: Mapped[str | None] = mapped_column(String(100), default=None)
    actor_type: Mapped[ActorType] = mapped_column(Enum(ActorType, name="audit_actor_type", native_enum=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    # Correlation only (X-Request-ID) — never treated as an auth signal
    # here either, matching app/core/middleware.py's own documented rule.
    request_id: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
