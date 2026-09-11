// Typed service functions for the Content contract consumed by MVP-05F.
// Mirrors apps/api/app/content/router.py exactly — both routes are GET-only;
// no write endpoint exists on the backend at all (no approval, production,
// or distribution route to call).

import { request } from "@/lib/api/client";
import type { ContentPieceDetailResponse, ContentPieceListResponse } from "@/types/content";

export async function listContent(campaignPublicId: string): Promise<ContentPieceListResponse> {
  return request<ContentPieceListResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/content`, {
    method: "GET",
  });
}

export async function getContentDetail(
  campaignPublicId: string,
  contentPublicId: string,
): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/content/${encodeURIComponent(contentPublicId)}`,
    { method: "GET" },
  );
}
