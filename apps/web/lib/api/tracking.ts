// Typed service function for the Tracking contract consumed by MVP-08B.
// Mirrors apps/api/app/tracking/router.py's GET route only — the backend
// also exposes a PATCH route (Plan transition / Requirement status
// update), but this read-only frontend slice deliberately never calls
// it: no write capability is exposed here.

import { request } from "@/lib/api/client";
import type { TrackingResponse } from "@/types/tracking";

export async function getTracking(campaignPublicId: string): Promise<TrackingResponse> {
  return request<TrackingResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/tracking`, {
    method: "GET",
  });
}
