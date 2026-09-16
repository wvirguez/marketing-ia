"""Human qualification only. VALIDATED is bounded acceptance, not replicated
fact, universal truth, causal proof, statistical significance, experiment
validation or independent system confirmation. Replication is never verified
by the system; confidence is categorical human judgment, not probability.
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.learning.models import QualificationConfidence, ReplicationStatus, EvidenceRelationship, EvidenceRemovalReason


class QualificationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confidence: QualificationConfidence | None = None
    replication_status: ReplicationStatus | None = None
    scope: str | None = Field(default=None, max_length=4000)
    generalization_boundary: str | None = Field(default=None, max_length=4000)


class QualificationAttach(BaseModel):
    model_config = ConfigDict(extra="forbid")
    performance_signal_id: str = Field(min_length=1, max_length=20)
    relationship: EvidenceRelationship
    note: str | None = Field(default=None, max_length=4000)
    replication_status: ReplicationStatus | None = None

    @model_validator(mode="after")
    def require_contradiction_note(self):
        if self.relationship == EvidenceRelationship.CONTRADICTING and (not self.note or not self.note.strip()):
            raise ValueError("Contradicting evidence requires a non-empty note.")
        return self


class QualificationDispose(BaseModel):
    model_config = ConfigDict(extra="forbid")
    removal_reason: EvidenceRemovalReason
    removal_note: str = Field(min_length=1, max_length=4000)
    replication_status: ReplicationStatus | None = None

    @model_validator(mode="after")
    def require_removal_note(self):
        if not self.removal_note.strip():
            raise ValueError("Disposition requires a non-empty note.")
        return self


class QualificationEvidencePublic(BaseModel):
    performance_signal_id: str
    relationship: EvidenceRelationship
    note: str | None
    created_at: datetime
    removed_at: datetime | None
    removal_reason: EvidenceRemovalReason | None
    removal_note: str | None
    removed_by_user_id: str | None
    governance_effective: bool
    blocks_validation: bool


class QualificationPublic(BaseModel):
    """Replication is a human claim only, never system-verified. All evidence
    history is visible, including OTHER dispositions that still block validation.
    The signal ID lists represent governance-effective additional evidence.
    """
    confidence: QualificationConfidence | None
    replication_status: ReplicationStatus | None
    consistency_status: Literal["NOT_ASSESSED", "SUPPORTING_ONLY", "MIXED", "CONTRADICTING"]
    scope: str | None
    generalization_boundary: str | None
    supporting_signal_ids: list[str]
    contradicting_signal_ids: list[str]
    evidence: list[QualificationEvidencePublic]
    validation_blockers: list[str]
    created_at: datetime
    updated_at: datetime
