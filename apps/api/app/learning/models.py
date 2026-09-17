"""Learning bounded context — BACKEND-14, extended by MVP-12B.

Persists the entities the BACKEND-14 Governance Freeze authorizes
(``LearningCandidate``, ``StrategicRecommendationCandidate``) plus one
additive bridge-provenance entity authorized by MVP-12B-A/-R1
(``LearningDerivation``). Provisional/Validated Learning are **statuses of
the same LearningCandidate row**, not separate tables (domain-model §1
note). Strategic Decision, CampaignVersion creation, object storage, AI
execution, and any Distribution/Paid Media/Tracking concept are all
explicitly deferred — nothing below implements, references, or invents
any of them.

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

from sqlalchemy import CheckConstraint, Index, text, DateTime, Enum, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint, func
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
        # MVP-26/26A-R1: nullable — existing rows predate StrategicImplication
        # and must never be backfilled with an invented one. Nullability is a
        # legacy-storage fact only; new-write governance (every production
        # Recommendation must reference an Implication) is enforced in the
        # service/API layers (app/learning/service.py, schemas.py), never here
        # — a NOT NULL constraint would make every legacy row unrepresentable.
        ForeignKeyConstraint(
            ["strategic_implication_id", "workspace_id"],
            ["strategic_implications.id", "strategic_implications.workspace_id"],
            name="fk_strategic_recommendation_candidates_strategic_implication_ws",
        ),
        # No UNIQUE(id, workspace_id) here — no concrete FK in this domain
        # targets it (Governance Freeze §30: no speculative candidate key,
        # the same discipline BACKEND-13 Phase 2R applied to Asset).
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    learning_candidate_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    # MVP-26/26A-R1: nullable for legacy rows only (see __table_args__ note
    # above) — every new write is required, at the service/API layer, to
    # supply a real StrategicImplication belonging to the same
    # LearningCandidate; this column being nullable is a storage fact, not
    # a new-write permission.
    strategic_implication_id: Mapped[uuid.UUID | None] = mapped_column(default=None, index=True)
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


class StrategicImplication(Base, UUIDPrimaryKeyMixin):
    """MVP-26/26A-R1: a bounded, human-authored interpretation of what one
    specific VALIDATED, sufficiently-qualified LearningCandidate means for
    strategic consideration within that Learning's own scope/
    generalization boundary — never itself a StrategicRecommendationCandidate,
    a Strategic Decision, a Strategic Approval, a Strategy mutation, a
    Strategic Maturity change, or a causal/commercial/experimental claim.

    Immutable from INSERT (no PATCH/PUT/DELETE, no status column) — the
    same "one narrative field, canonical silence beyond that" precedent
    already used by LearningCandidate.summary/StrategicRecommendationCandidate.summary.
    References LearningCandidate only; LearningQualification is reached by
    traversal and is already frozen once the candidate is VALIDATED
    (QualificationService.lock()), so no field is copied/snapshotted here
    (MVP-26A §I, proven, not merely asserted).

    Cardinality: LearningCandidate 1 -> 0..N StrategicImplication — no
    UNIQUE(learning_candidate_id), mirroring the identical, already-
    established StrategicRecommendationCandidate cardinality exactly.
    """

    __tablename__ = "strategic_implications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["learning_candidate_id", "workspace_id"],
            ["learning_candidates.id", "learning_candidates.workspace_id"],
            name="fk_strategic_implications_learning_candidate_ws",
        ),
        # Candidate key required so StrategicRecommendationCandidate can
        # declare a composite, tenant-safe FK on
        # (strategic_implication_id, workspace_id) — identical reasoning to
        # LearningCandidate's own uq_learning_candidates_id_workspace_id.
        UniqueConstraint("id", "workspace_id", name="uq_strategic_implications_id_workspace_id"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    learning_candidate_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by the composite FK above
    statement: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LearningDerivation(Base, UUIDPrimaryKeyMixin):
    """Bridge-specific identity + creator provenance for a LearningCandidate
    produced by the explicit Measurement -> Learning bridge (MVP-12B) —
    living entirely outside LearningCandidate itself, mirroring
    ``app/measurement/models.py``'s own MeasurementObservationDerivation/
    MeasurementAnalysisRunResult pattern exactly. A LearningCandidate
    created by any OTHER path (manual, a future second bridge/agent
    runtime) simply has no row here — that absence, not a flag value, is
    what distinguishes a bridge-created candidate from any other.

    ``UNIQUE(analysis_result_id)`` enforces "at most one bridge derivation
    per AnalysisResult" (the bridge's own narrower contract) without
    touching LearningCandidate's frozen schema or its global 0..N-per-
    AnalysisResult cardinality at all. ``UNIQUE(learning_candidate_id)``
    makes the mapping 1:1 in both directions, since a bridge-created
    candidate never has more than one derivation row by construction.

    No ``derivation_kind``/``source`` column: this table is single-purpose
    by construction — every row here IS a Measurement -> Learning bridge
    derivation. A second future source would earn its own dedicated
    table under its own separately-authorized gate, not a speculative
    enum value added here today (MVP-12B-A-R1)."""

    __tablename__ = "learning_derivations"
    __table_args__ = (
        UniqueConstraint("analysis_result_id", name="uq_learning_derivations_analysis_result_id"),
        UniqueConstraint("learning_candidate_id", name="uq_learning_derivations_learning_candidate_id"),
        ForeignKeyConstraint(
            ["analysis_result_id", "workspace_id"],
            ["analysis_results.id", "analysis_results.workspace_id"],
            name="fk_learning_derivations_analysis_result_workspace",
        ),
        ForeignKeyConstraint(
            ["learning_candidate_id", "workspace_id"],
            ["learning_candidates.id", "learning_candidates.workspace_id"],
            name="fk_learning_derivations_learning_candidate_workspace",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    analysis_result_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by composite FK + UNIQUE
    learning_candidate_id: Mapped[uuid.UUID] = mapped_column(index=True)  # covered by composite FK + UNIQUE
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# MVP-25: qualification is separate from the candidate lifecycle. VALIDATED
# means bounded acceptance under current evidence, never causal proof,
# statistical significance, experiment validation or system-verified replication.
class QualificationConfidence(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ReplicationStatus(str, enum.Enum):
    REPLICATION_NOT_ESTABLISHED = "REPLICATION_NOT_ESTABLISHED"
    REPLICATION_EVIDENCE_PRESENT = "REPLICATION_EVIDENCE_PRESENT"
    REPLICATION_FAILED = "REPLICATION_FAILED"


class EvidenceRelationship(str, enum.Enum):
    SUPPORTING = "SUPPORTING"
    CONTRADICTING = "CONTRADICTING"


class EvidenceRemovalReason(str, enum.Enum):
    ATTACHMENT_ERROR = "ATTACHMENT_ERROR"
    OTHER = "OTHER"


class LearningQualification(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "learning_qualifications"
    __table_args__ = (
        UniqueConstraint("learning_candidate_id", name="uq_lq_candidate"),
        UniqueConstraint("id", "workspace_id", name="uq_lq_id_workspace"),
        ForeignKeyConstraint(["learning_candidate_id", "workspace_id"],
                             ["learning_candidates.id", "learning_candidates.workspace_id"],
                             name="fk_lq_candidate_workspace"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    learning_candidate_id: Mapped[uuid.UUID] = mapped_column(index=True)
    confidence: Mapped[QualificationConfidence | None] = mapped_column(Enum(QualificationConfidence, name="learning_qualification_confidence"))
    replication_status: Mapped[ReplicationStatus | None] = mapped_column(
        Enum(ReplicationStatus, name="learning_qualification_replication_status"),
        default=ReplicationStatus.REPLICATION_NOT_ESTABLISHED,
        server_default=ReplicationStatus.REPLICATION_NOT_ESTABLISHED.value,
    )
    scope: Mapped[str | None] = mapped_column(Text)
    generalization_boundary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LearningQualificationSignal(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "learning_qualification_signals"
    __table_args__ = (
        ForeignKeyConstraint(["learning_qualification_id", "workspace_id"],
                             ["learning_qualifications.id", "learning_qualifications.workspace_id"],
                             name="fk_lqs_qualification_workspace"),
        ForeignKeyConstraint(["performance_signal_id", "workspace_id"],
                             ["performance_signals.id", "performance_signals.workspace_id"],
                             name="fk_lqs_signal_workspace"),
        CheckConstraint(
            "(removed_at IS NULL AND removal_reason IS NULL AND removal_note IS NULL AND removed_by_user_id IS NULL) OR "
            "(removed_at IS NOT NULL AND removal_reason IS NOT NULL AND removal_note IS NOT NULL AND removed_by_user_id IS NOT NULL)",
            name="disposition_complete",
        ),
        CheckConstraint("relationship != 'CONTRADICTING' OR (note IS NOT NULL AND length(trim(note)) > 0)", name="contradiction_note"),
        CheckConstraint("removal_note IS NULL OR length(trim(removal_note)) > 0", name="removal_note_nonempty"),
        Index("uq_lqs_active_pair", "learning_qualification_id", "performance_signal_id", unique=True,
              postgresql_where=text("removed_at IS NULL")),
        # OTHER remains effective for both relationships. A second partial
        # index closes the historical-disposition hole in active-only uniqueness.
        Index("uq_lqs_effective_pair", "learning_qualification_id", "performance_signal_id", unique=True,
              postgresql_where=text("removal_reason IS NULL OR removal_reason != 'ATTACHMENT_ERROR'")),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    learning_qualification_id: Mapped[uuid.UUID] = mapped_column(index=True)
    performance_signal_id: Mapped[uuid.UUID] = mapped_column(index=True)
    relationship: Mapped[EvidenceRelationship] = mapped_column(Enum(EvidenceRelationship, name="learning_qualification_signal_relationship"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removal_reason: Mapped[EvidenceRemovalReason | None] = mapped_column(Enum(EvidenceRemovalReason, name="learning_qualification_signal_removal_reason"))
    removal_note: Mapped[str | None] = mapped_column(Text)
    removed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
