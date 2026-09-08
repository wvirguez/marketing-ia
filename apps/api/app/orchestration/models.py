"""Orchestration foundation — BACKEND-06.

Persists a business-stage workflow structure for a ``CampaignRun``
(``RunStageExecution``) and the human-in-the-loop scaffolding
(``HumanDecisionRequest``/``HumanDecisionResponse``, named exactly as in
`docs/backend/BACKEND-01-ARCHITECTURE.md` §4).

ORCHESTRATION FOUNDATION != AI EXECUTION: nothing in this module ever
calls an AI provider, invokes an agent, or produces a real specialist
output. A ``RunStageExecution`` only ever tracks *that a stage exists in
this run's workflow and what lifecycle state it is in* — never a
generated payload. See ``app/orchestration/service.py`` for the
transition rules that are the actual guarantee behind that sentence.

Relationship to BACKEND-01's "Campaign Phase" (domain-model doc, §1,
Campaign entity table): that entity is explicitly modeled as a
*derived, display-only* value ("which one phase is currently active"),
deliberately not its own stored table, to avoid a second source of
truth once it can be computed from Agent Run + Agent Definition data
(BACKEND-07+). ``RunStageExecution`` is not that field — no Agent Run
exists yet to derive anything from. It is new ground: the persisted
*workflow structure* itself (which stages exist, in what order, in what
state), which a future "current phase" projection can read from without
creating a second, competing fact — reading the currently non-terminal
stage from this table *is* the derivation, not a duplicate of it.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BusinessStage(str, enum.Enum):
    """Public, business-language stage vocabulary (BACKEND-06 §7) — never
    an internal ``AGENT-0N`` identifier. Ordered per
    ``BUSINESS_STAGE_ORDER`` below, matching the module boundary already
    proposed in `docs/backend/BACKEND-01-ARCHITECTURE.md` §9
    (research/strategy/planning/content/assets/distribution/paid_media/
    tracking/measurement/learning) and the `ProgressProjector` mapping
    in §3 of that document (AGENT-01=research, AGENT-02=audience,
    AGENT-03=strategy, AGENT-04=plan, AGENT-05=content/creative,
    AGENT-06=distribution, AGENT-09=paid media, AGENT-10=tracking;
    measurement/learning follow the same module list)."""

    RESEARCH = "RESEARCH"
    AUDIENCE = "AUDIENCE"
    STRATEGY = "STRATEGY"
    PLAN = "PLAN"
    CONTENT = "CONTENT"
    CREATIVE = "CREATIVE"
    DISTRIBUTION = "DISTRIBUTION"
    PAID_MEDIA = "PAID_MEDIA"
    TRACKING = "TRACKING"
    MEASUREMENT = "MEASUREMENT"
    LEARNING = "LEARNING"


BUSINESS_STAGE_ORDER: tuple[BusinessStage, ...] = (
    BusinessStage.RESEARCH,
    BusinessStage.AUDIENCE,
    BusinessStage.STRATEGY,
    BusinessStage.PLAN,
    BusinessStage.CONTENT,
    BusinessStage.CREATIVE,
    BusinessStage.DISTRIBUTION,
    BusinessStage.PAID_MEDIA,
    BusinessStage.TRACKING,
    BusinessStage.MEASUREMENT,
    BusinessStage.LEARNING,
)


class StageExecutionStatus(str, enum.Enum):
    """BACKEND-06 §11. Terminal states (``COMPLETED``/``FAILED``/
    ``SKIPPED``/``CANCELLED``) have no outgoing edges in the transition
    matrix (``app/orchestration/transitions.py``) — enforced there, not
    just documented here."""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class RunStageExecution(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "run_stage_executions"
    __table_args__ = (
        Index("uq_run_stage_executions_run_stage", "campaign_run_id", "stage", unique=True),
        Index("uq_run_stage_executions_run_ordinal", "campaign_run_id", "ordinal", unique=True),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    # Direct, not "via campaign_run" — matches the CampaignRun precedent
    # (BACKEND-05) of tenant-owned rows carrying their own workspace_id
    # rather than only being reachable by traversal. Always derived from
    # the parent CampaignRun at creation time, never an independent
    # input (see app/orchestration/repository.py).
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_runs.id"), index=True)
    stage: Mapped[BusinessStage] = mapped_column(Enum(BusinessStage, name="business_stage", native_enum=True))
    # Immutable, persisted ordering — never derived from insertion order.
    ordinal: Mapped[int] = mapped_column()
    status: Mapped[StageExecutionStatus] = mapped_column(
        Enum(StageExecutionStatus, name="stage_execution_status", native_enum=True),
        default=StageExecutionStatus.PENDING,
        server_default=StageExecutionStatus.PENDING.value,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Short, human-readable context only — never a payload, never a
    # generated output (BACKEND-06 §10/§27).
    blocked_reason: Mapped[str | None] = mapped_column(String(500), default=None)
    failure_reason: Mapped[str | None] = mapped_column(String(500), default=None)


class DecisionRequestStatus(str, enum.Enum):
    """Exact BACKEND-01 terminology (`docs/backend/BACKEND-01-ARCHITECTURE.md`
    §4): ``OPEN → RESOLVED | EXPIRED | CANCELLED``. BACKEND-06 never
    itself transitions a request to ``EXPIRED`` — that policy is
    explicitly deferred by BACKEND-01 itself ("policy TBD in a later
    stage"); the value exists for schema completeness only."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class HumanDecisionRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """BACKEND-01 domain-model doc: "Human Decision Request... belongs to
    Orchestration Run", tenant = Workspace (direct). No direct
    ``campaign_id`` column — deliberately, matching that exact
    relationship; a response's campaign is always reached by joining
    through its ``campaign_run``, never duplicated."""

    __tablename__ = "human_decision_requests"

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    campaign_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_runs.id"), index=True)
    stage_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("run_stage_executions.id"), default=None, index=True
    )
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[DecisionRequestStatus] = mapped_column(
        Enum(DecisionRequestStatus, name="decision_request_status", native_enum=True),
        default=DecisionRequestStatus.OPEN,
        server_default=DecisionRequestStatus.OPEN.value,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class HumanDecisionResponse(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One response per request in this stage (BACKEND-06 §17 MVP
    choice, consistent with BACKEND-01 §4: "RESOLVED requires exactly
    one Human Decision Response") — enforced by the unique index below,
    not only by the service-layer status check."""

    __tablename__ = "human_decision_responses"
    __table_args__ = (
        Index("uq_human_decision_responses_request", "decision_request_id", unique=True),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    decision_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("human_decision_requests.id"), index=True)
    responded_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    response_text: Mapped[str] = mapped_column(Text)
