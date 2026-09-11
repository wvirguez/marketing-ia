// Typed service function for the Planning contract consumed by MVP-05C.
// Mirrors apps/api/app/planning/router.py exactly — GET-only; no write
// endpoint exists on the backend at all.

import { request } from "@/lib/api/client";
import type { PlanOutputResponse } from "@/types/planning";

export async function getPlan(campaignPublicId: string): Promise<PlanOutputResponse> {
  return request<PlanOutputResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/plan`, {
    method: "GET",
  });
}
