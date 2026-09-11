// Typed service functions for the Research/Audience contracts consumed by
// MVP-05B. Mirrors apps/api/app/research/router.py exactly — both routes
// are GET-only; no write endpoint exists on the backend at all, so none
// is wrapped here.

import { request } from "@/lib/api/client";
import type { AudienceOutputResponse, ResearchOutputResponse } from "@/types/research";

export async function getResearch(campaignPublicId: string): Promise<ResearchOutputResponse> {
  return request<ResearchOutputResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/research`, {
    method: "GET",
  });
}

export async function getAudience(campaignPublicId: string): Promise<AudienceOutputResponse> {
  return request<AudienceOutputResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/audience`, {
    method: "GET",
  });
}
