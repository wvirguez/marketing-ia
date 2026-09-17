// Typed service functions for the Learning contract. Mirrors
// apps/api/app/learning/router.py exactly (MVP-12C, extended by MVP-23B,
// MVP-25, MVP-26): GET /learning, POST /learning/derive,
// POST /learning/{id}/mark-provisional, POST /learning/{id}/mark-validation-pending,
// POST /learning/{id}/decision, POST /learning/{id}/strategic-implications,
// POST /learning/{id}/recommendations, PATCH /learning/{recommendation_id}.

import { request } from "@/lib/api/client";
import type {
  QualificationPatch,
  QualificationAttach,
  QualificationDispose,
  LearningCandidatePublic,
  LearningCandidateStatus,
  LearningResponse,
  StrategicImplicationPublic,
  StrategicRecommendationCandidatePublic,
  StrategicRecommendationDecision,
} from "@/types/learning";

function learningPath(campaignPublicId: string, suffix = ""): string {
  return `/campaigns/${encodeURIComponent(campaignPublicId)}/learning${suffix}`;
}

export async function getCampaignLearning(campaignPublicId: string): Promise<LearningResponse> {
  return request<LearningResponse>(learningPath(campaignPublicId), { method: "GET" });
}

export async function deriveCampaignLearning(campaignPublicId: string): Promise<LearningResponse> {
  return request<LearningResponse>(learningPath(campaignPublicId, "/derive"), { method: "POST" });
}

export async function markLearningCandidateProvisional(
  campaignPublicId: string,
  learningCandidatePublicId: string,
): Promise<LearningCandidatePublic> {
  return request<LearningCandidatePublic>(
    learningPath(campaignPublicId, `/${encodeURIComponent(learningCandidatePublicId)}/mark-provisional`),
    { method: "POST" },
  );
}

export async function markLearningCandidateValidationPending(
  campaignPublicId: string,
  learningCandidatePublicId: string,
): Promise<LearningCandidatePublic> {
  return request<LearningCandidatePublic>(
    learningPath(campaignPublicId, `/${encodeURIComponent(learningCandidatePublicId)}/mark-validation-pending`),
    { method: "POST" },
  );
}

export async function decideLearningCandidate(
  campaignPublicId: string,
  learningCandidatePublicId: string,
  decision: Extract<LearningCandidateStatus, "VALIDATED" | "REJECTED" | "INSUFFICIENT_EVIDENCE">,
): Promise<LearningCandidatePublic> {
  return request<LearningCandidatePublic>(
    learningPath(campaignPublicId, `/${encodeURIComponent(learningCandidatePublicId)}/decision`),
    { method: "POST", body: { decision } },
  );
}

export async function createStrategicImplication(
  campaignPublicId: string,
  learningCandidatePublicId: string,
  statement: string,
): Promise<StrategicImplicationPublic> {
  return request<StrategicImplicationPublic>(
    learningPath(campaignPublicId, `/${encodeURIComponent(learningCandidatePublicId)}/strategic-implications`),
    { method: "POST", body: { statement } },
  );
}

export async function createStrategicRecommendation(
  campaignPublicId: string,
  learningCandidatePublicId: string,
  strategicImplicationId: string,
  summary: string,
): Promise<StrategicRecommendationCandidatePublic> {
  return request<StrategicRecommendationCandidatePublic>(
    learningPath(campaignPublicId, `/${encodeURIComponent(learningCandidatePublicId)}/recommendations`),
    { method: "POST", body: { strategic_implication_id: strategicImplicationId, summary } },
  );
}

export async function decideStrategicRecommendation(
  campaignPublicId: string,
  recommendationPublicId: string,
  decision: StrategicRecommendationDecision,
): Promise<StrategicRecommendationCandidatePublic> {
  return request<StrategicRecommendationCandidatePublic>(
    learningPath(campaignPublicId, `/${encodeURIComponent(recommendationPublicId)}`),
    { method: "PATCH", body: { decision } },
  );
}

export function updateLearningQualification(campaignId: string, candidateId: string, body: QualificationPatch) {
  return request<LearningCandidatePublic>(learningPath(campaignId, `/${encodeURIComponent(candidateId)}/qualification`), { method: "PATCH", body });
}
export function attachLearningEvidence(campaignId: string, candidateId: string, body: QualificationAttach) {
  return request<LearningCandidatePublic>(learningPath(campaignId, `/${encodeURIComponent(candidateId)}/qualification/signals`), { method: "POST", body });
}
export function disposeLearningEvidence(campaignId: string, candidateId: string, signalId: string, body: QualificationDispose) {
  return request<LearningCandidatePublic>(learningPath(campaignId, `/${encodeURIComponent(candidateId)}/qualification/signals/${encodeURIComponent(signalId)}/dispose`), { method: "POST", body });
}
