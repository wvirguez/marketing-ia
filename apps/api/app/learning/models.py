"""Learning bounded context — BACKEND-14.

Persists exactly the two entities the BACKEND-14 Governance Freeze
authorizes: ``LearningCandidate``, ``StrategicRecommendationCandidate``.
Provisional/Validated Learning are **statuses of the same LearningCandidate
row**, not separate tables (domain-model §1 note). Strategic Decision,
CampaignVersion creation, object storage, AI execution, and any Distribution/
Paid Media/Tracking concept are all explicitly deferred — nothing below
implements, references, or invents any of them.

**Ownership (frozen):** ``AnalysisResult 1 -> 0..N LearningCandidate`` via a
plain FK (``analysis_result_id``) — no association table, no
``UNIQUE(analysis_result_id)``, since (unlike ``PerformanceSignal ->
AnalysisResult``) a ``LearningCandidate`` is the new row being created, so
setting its FK once at creation time is never a retroactive mutation of the
already-existing, immutable ``AnalysisResult``. ``LearningCandidate 1 ->
0..N StrategicRecommendationCandidate`` via a plain FK
(``learning_candidate_id``), same reasoning, no uniqueness invented.

**Tenancy:** direct, un-qualified ``workspace_id`` on both (domain-model:
"Workspace"), each with a composite tenant-safe FK to its named parent.
``LearningCandidate`` additionally declares ``UNIQUE(id, workspace_id)`` —
required because ``StrategicRecommendationCandidate``'s own composite FK
consumes it. ``StrategicRecommendationCandidate`` does NOT declare that
candidate key — no concrete FK in this domain targets it (Governance Freeze
§Z/§30 — no speculative candidate key).

No ``campaign_id``/``campaign_run_id``/``stage_execution_id``/
``experiment_id``/``content_piece_id``/``strategy_id`` is duplicated on
either table — Campaign ancestry is derived by traversal
(``LearningCandidate -> AnalysisResult -> Campaign``), never stored
redundantly (Governance Freeze §F).

**LearningCandidate** is mutable only via its frozen status transition
graph (see ``app/learning/transitions.py``); ``summary``,
``analysis_result_id``, ``workspace_id``, ``public_id`` are all immutable
after creation. No ``updated_at`` — the closer structural precedent is
``ContentPiece``/``ContentApproval`` (both graph-driven, neither carries a
generic ``updated_at``; their own dedicated AuditEvent per transition is
the authoritative "when did this change" record, and ``LearningCandidate``
gets the identical treatment via ``learning.candidate.status_changed``).

**StrategicRecommendationCandidate** is mutable only via its one-shot
``decision`` (NULL -> ACCEPTED/REJECTED, never reversed, never decided
twice); ``decided_at`` is set exactly once, a direct structural analog to
``ContentApproval.decided_at``.

PERSISTING A LEARNING CANDIDATE != VALIDATED LEARNING. AN ACCEPTED
STRATEGIC RECOMMENDATION CANDIDATE != A STRATEGIC DECISION != AN AUTOMATIC
STRATEGY MUTATION != AN AUTOMATIC CAMPAIGNVERSION. Nothing below mutates
``AnalysisResult``, ``Strategy``, ``Campaign``, ``Content``, or ``Assets``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, UUIDPrimaryKeyMixin


class LearningCandidateStatus(str, enum.Enum):
    """Exact BACKEND-01 vocabulary, Learning Lifecycle state machine
    (`docs/backend/BACKEND-01-ARCHITECTURE.md` §2I) — no value renamed,
    none added. See ``app/learning/transitions.py`` for the exact legal
    edges."""

    CANDIDATE_IDENTIFIED = "CANDIDATE_IDENTIFIED"
    PROVISIONAL = "PROVISIONAL"
    VALIDATION_PENDING = "VALIDATION_PENDING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class LearningCandidate(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "learning_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_result_id", "workspace_id"],
            ["analysis_results.id", "analysis_results.workspace_id"],
            name="fk_learning_candidates_analysis_result_workspace",
        ),
        # Governance Freeze (explicitly authorized, additive-only): a
        # candidate key purely so StrategicRecommendationCandidate can
        # declare a composite, tenant-safe FK on (learning_candidate_id,
        # workspace_id) — the same pattern already established throughout
        # BACKEND-08/09/10/11/13.
        UniqueConstraint("id", "workspace_id", name="uq_learning_candidates_id_workspace_id"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    analysis_result_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    status: Mapped[LearningCandidateStatus] = mapped_column(
        Enum(LearningCandidateStatus, name="learning_candidate_status", native_enum=True),
        default=LearningCandidateStatus.CANDIDATE_IDENTIFIED,
        server_default=LearningCandidateStatus.CANDIDATE_IDENTIFIED.value,
    )
    # No canonical field structure beyond "the proposed learning" is named
    # anywhere — a single narrative field, mirroring the exact precedent
    # AnalysisResult.summary/PerformanceSignal.summary already establish
    # under identical canonical silence.
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StrategicRecommendationDecision(str, enum.Enum):
    """Exact BACKEND-01 vocabulary (Architecture §2I: "ACCEPTED |
    REJECTED") — no third value. NULL on the column means "no decision
    yet"; it is never itself a member of this enum (Governance Freeze
    §F/§M — do not invent PENDING/OPEN/PROPOSED/UNDER_REVIEW)."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class StrategicRecommendationCandidate(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "strategic_recommendation_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["learning_candidate_id", "workspace_id"],
            ["learning_candidates.id", "learning_candidates.workspace_id"],
            # Shortened from the naming convention's own default output —
            # that name exceeds PostgreSQL's 63-character identifier limit
            # (verified empirically: 67 chars). No other convention change.
            name="fk_strategic_recommendation_candidates_learning_candidate_ws",
        ),
        # No UNIQUE(id, workspace_id) here — no concrete FK in this domain
        # targets it (Governance Freeze §30: no speculative candidate key,
        # the same discipline BACKEND-13 Phase 2R applied to Asset).
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    learning_candidate_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    # No canonical field structure beyond "a proposed strategy change" is
    # named anywhere — same narrative-field precedent as
    # LearningCandidate.summary above. No Strategy FK, no CampaignVersion
    # reference — zero coupling into Strategy's own tables.
    summary: Mapped[str] = mapped_column(Text)
    # Nullable: NULL = undecided. Set exactly once, never rewritten
    # (Governance Freeze §F/§M/§15).
    decision: Mapped[StrategicRecommendationDecision | None] = mapped_column(
        Enum(StrategicRecommendationDecision, name="strategic_recommendation_decision", native_enum=True),
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Set exactly when `decision` moves from NULL to a terminal value — a
    # direct structural analog to ContentApproval.decided_at.
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
