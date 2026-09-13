// Mirrors apps/api/app/learning/schemas.py exactly (MVP-12C). No field here
// represents confidence, evidence count, replication status, validation
// score, or generalization boundary — none of those exist on the backend
// either. CANDIDATE_IDENTIFIED/PROVISIONAL/VALIDATION_PENDING are NOT
// validated learning — only VALIDATED is.

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
}

export type StrategicRecommendationDecision = "ACCEPTED" | "REJECTED";

export interface StrategicRecommendationCandidatePublic {
  id: string;
  learning_candidate_id: string;
  summary: string;
  decision: StrategicRecommendationDecision | null;
  created_at: string;
  decided_at: string | null;
}

export interface LearningResponse {
  learning_candidates: LearningCandidatePublic[];
  strategic_recommendation_candidates: StrategicRecommendationCandidatePublic[];
}
