export type LearningCandidateStatus =
  | "CANDIDATE_IDENTIFIED"
  | "PROVISIONAL"
  | "VALIDATION_PENDING"
  | "VALIDATED"
  | "REJECTED"
  | "INSUFFICIENT_EVIDENCE";

export interface LearningCandidatePublic {
  id: string;
  analysis_result_id: string;
  status: LearningCandidateStatus;
  summary: string;
  created_at: string;
  qualification: LearningQualificationPublic | null;
  strategic_implications: StrategicImplicationPublic[];
}

// MVP-26: a bounded, human-governed interpretation of a VALIDATED,
// sufficiently-qualified Learning. Not a Recommendation, Decision,
// Approval, or a causal/commercial claim.
export interface StrategicImplicationPublic {
  id: string;
  learning_candidate_id: string;
  statement: string;
  created_at: string;
}

export type StrategicRecommendationDecision = "ACCEPTED" | "REJECTED";

export interface StrategicRecommendationCandidatePublic {
  id: string;
  learning_candidate_id: string;
  // MVP-26/26A-R1: nullable only for rows created before this linkage was
  // required — every new Recommendation always has one.
  strategic_implication_id: string | null;
  summary: string;
  decision: StrategicRecommendationDecision | null;
  created_at: string;
  decided_at: string | null;
}

export interface LearningResponse {
  learning_candidates: LearningCandidatePublic[];
  strategic_recommendation_candidates: StrategicRecommendationCandidatePublic[];
}

// Human judgments only: confidence is not probability; replication is not system verified.
export type QualificationConfidence = "LOW" | "MEDIUM" | "HIGH";
export type ReplicationStatus = "REPLICATION_NOT_ESTABLISHED" | "REPLICATION_EVIDENCE_PRESENT" | "REPLICATION_FAILED";
export type EvidenceRelationship = "SUPPORTING" | "CONTRADICTING";
export type EvidenceRemovalReason = "ATTACHMENT_ERROR" | "OTHER";
export interface QualificationPatch {
  confidence?: QualificationConfidence | null;
  replication_status?: ReplicationStatus | null;
  scope?: string | null;
  generalization_boundary?: string | null;
}
export interface QualificationAttach {
  performance_signal_id: string;
  relationship: EvidenceRelationship;
  note?: string;
  replication_status?: ReplicationStatus | null;
}
export interface QualificationDispose {
  removal_reason: EvidenceRemovalReason;
  removal_note: string;
  replication_status?: ReplicationStatus | null;
}
export interface QualificationEvidencePublic {
  performance_signal_id: string;
  relationship: EvidenceRelationship;
  note: string | null;
  created_at: string;
  removed_at: string | null;
  removal_reason: EvidenceRemovalReason | null;
  removal_note: string | null;
  removed_by_user_id: string | null;
  governance_effective: boolean;
  blocks_validation: boolean;
}
export interface LearningQualificationPublic {
  confidence: QualificationConfidence | null;
  replication_status: ReplicationStatus | null;
  consistency_status: "NOT_ASSESSED" | "SUPPORTING_ONLY" | "MIXED" | "CONTRADICTING";
  scope: string | null;
  generalization_boundary: string | null;
  supporting_signal_ids: string[];
  contradicting_signal_ids: string[];
  evidence: QualificationEvidencePublic[];
  validation_blockers: string[];
  created_at: string;
  updated_at: string;
}
