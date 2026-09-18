// Typed service functions for the Planning contract. Mirrors
// apps/api/app/planning/router.py exactly — GET (read) plus, since
// MVP-33B, the one governed POST write (frozen MVP-33A/-33A-R1 contract).

import { request } from "@/lib/api/client";
import type {
  ContentBriefPublic,
  ContentPlanPublic,
  CreateContentBriefRequest,
  CreateContentPlanRequest,
  PlanOutputResponse,
} from "@/types/planning";

export async function getPlan(campaignPublicId: string): Promise<PlanOutputResponse> {
  return request<PlanOutputResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/plan`, {
    method: "GET",
  });
}

export async function createPlan(
  campaignPublicId: string,
  summary: string,
  experimentPublicId: string | null,
): Promise<ContentPlanPublic> {
  const payload: CreateContentPlanRequest = { summary, experiment_public_id: experimentPublicId };
  const response = await request<PlanOutputResponse>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/plan`,
    { method: "POST", body: payload },
  );
  return response.plan as ContentPlanPublic;
}

// MVP-34A/-34B: governed, create-only Content Brief — mirrors
// apps/api/app/content/router.py::create_content_brief exactly.
export async function createBrief(
  campaignPublicId: string,
  planItemPublicId: string,
  brief: string,
): Promise<ContentBriefPublic> {
  const payload: CreateContentBriefRequest = { brief };
  return request<ContentBriefPublic>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/plan/items/${encodeURIComponent(planItemPublicId)}/brief`,
    { method: "POST", body: payload },
  );
}
