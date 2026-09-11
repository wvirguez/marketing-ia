// Typed service functions for the Orchestration contracts consumed by
// MVP-05A. Every shape here is read directly from the actual backend
// router/schemas (apps/api/app/orchestration/router.py, schemas.py) — no
// speculative fields.
//
// Only the three operations MVP-05A needs are wrapped: initialize, start,
// and progress. `/stages` is not wrapped separately — `RunProgressPublic`
// already embeds the full `stages[]` array, so a dedicated wrapper would
// be unused API surface. `/events` and `/decisions` are out of this
// phase's scope.

import { request } from "@/lib/api/client";
import type { CampaignRunPublic } from "@/types/campaign";
import type { RunProgressPublic, StageExecutionListResponse } from "@/types/orchestration";

function runPath(campaignPublicId: string, runPublicId: string, suffix: string): string {
  return `/campaigns/${encodeURIComponent(campaignPublicId)}/runs/${encodeURIComponent(runPublicId)}${suffix}`;
}

export async function initializeCampaignRun(
  campaignPublicId: string,
  runPublicId: string,
): Promise<StageExecutionListResponse> {
  return request<StageExecutionListResponse>(runPath(campaignPublicId, runPublicId, "/initialize"), {
    method: "POST",
  });
}

export async function startCampaignRun(campaignPublicId: string, runPublicId: string): Promise<CampaignRunPublic> {
  return request<CampaignRunPublic>(runPath(campaignPublicId, runPublicId, "/start"), { method: "POST" });
}

export async function getCampaignRunProgress(
  campaignPublicId: string,
  runPublicId: string,
): Promise<RunProgressPublic> {
  return request<RunProgressPublic>(runPath(campaignPublicId, runPublicId, "/progress"), { method: "GET" });
}
