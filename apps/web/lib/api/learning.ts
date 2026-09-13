// Typed service functions for the Learning contract. Mirrors
// apps/api/app/learning/router.py's GET /learning and POST /learning/derive
// (MVP-12C). PATCH /learning/{recommendation_public_id} is deliberately
// unused — no recommendation-decision UI exists yet, and no current
// production flow creates a StrategicRecommendationCandidate to decide on.

import { request } from "@/lib/api/client";
import type { LearningResponse } from "@/types/learning";

export async function getCampaignLearning(campaignPublicId: string): Promise<LearningResponse> {
  return request<LearningResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/learning`, {
    method: "GET",
  });
}

export async function deriveCampaignLearning(campaignPublicId: string): Promise<LearningResponse> {
  return request<LearningResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/learning/derive`, {
    method: "POST",
  });
}
