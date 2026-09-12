// Typed service function for the Assets contract consumed by MVP-07B.
// Mirrors apps/api/app/assets/router.py exactly — the single route is
// GET-only; no write endpoint exists on the backend at all (no creation,
// versioning, or archive route to call from the frontend).

import { request } from "@/lib/api/client";
import type { AssetsForContentPieceResponse } from "@/types/assets";

export async function getAssetsForContent(
  campaignPublicId: string,
  contentPublicId: string,
): Promise<AssetsForContentPieceResponse> {
  return request<AssetsForContentPieceResponse>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/content/${encodeURIComponent(contentPublicId)}/assets`,
    { method: "GET" },
  );
}
