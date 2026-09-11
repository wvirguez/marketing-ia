// Typed service function for the Strategy contract consumed by MVP-05C.
// Mirrors apps/api/app/strategy/router.py exactly — GET-only; no write
// endpoint exists on the backend at all.

import { request } from "@/lib/api/client";
import type { StrategyOutputResponse } from "@/types/strategy";

export async function getStrategy(campaignPublicId: string): Promise<StrategyOutputResponse> {
  return request<StrategyOutputResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/strategy`, {
    method: "GET",
  });
}
