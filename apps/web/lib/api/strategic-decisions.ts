// Typed service functions for the StrategicDecision contract. Mirrors
// apps/api/app/orchestration/strategic_decision_router.py exactly (MVP-28B):
// GET/POST /strategic-decisions, POST /strategic-decisions/{id}/supersede.

import { request } from "@/lib/api/client";
import type { StrategicDecisionPublic, StrategicDecisionType } from "@/types/strategic-decisions";

function campaignPath(campaignPublicId: string, suffix: string): string {
  return `/campaigns/${encodeURIComponent(campaignPublicId)}${suffix}`;
}

export async function getStrategicDecisions(campaignPublicId: string): Promise<StrategicDecisionPublic[]> {
  const response = await request<{ items: StrategicDecisionPublic[] }>(
    campaignPath(campaignPublicId, "/strategic-decisions"),
    { method: "GET" },
  );
  return response.items;
}

// Records the first (or a subsequent, independent-of-supersession)
// StrategicDecision for one accepted StrategicRecommendationCandidate.
export async function recordStrategicDecision(
  campaignPublicId: string,
  recommendationPublicId: string,
  decisionType: StrategicDecisionType,
  statement: string,
): Promise<StrategicDecisionPublic> {
  return request<StrategicDecisionPublic>(campaignPath(campaignPublicId, "/strategic-decisions"), {
    method: "POST",
    body: { strategic_recommendation_candidate_id: recommendationPublicId, decision_type: decisionType, statement },
  });
}

// Atomically supersedes one specific existing StrategicDecision with a
// fresh replacement — distinct from recordStrategicDecision above. The
// replacement always shares the original's own Recommendation server-side;
// no Recommendation id is sent here.
export async function supersedeStrategicDecision(
  campaignPublicId: string,
  decisionPublicId: string,
  decisionType: StrategicDecisionType,
  statement: string,
): Promise<StrategicDecisionPublic> {
  return request<StrategicDecisionPublic>(
    campaignPath(campaignPublicId, `/strategic-decisions/${encodeURIComponent(decisionPublicId)}/supersede`),
    { method: "POST", body: { decision_type: decisionType, statement } },
  );
}
