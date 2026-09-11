// Typed service functions for the Campaign contracts consumed by MVP-03.
// Every shape here is read directly from the actual backend router/schemas
// (apps/api/app/campaigns/router.py, schemas.py) — no speculative fields.
//
// Only the four operations the real UI needs are implemented:
// list/create/get/list-runs. `PATCH`/`archive` exist on the backend but
// are not exercised by any MVP-03 screen, so they are intentionally not
// wrapped here (no unused API surface).

import { request } from "@/lib/api/client";
import type {
  CampaignCreateRequest,
  CampaignCreateResponse,
  CampaignListResponse,
  CampaignPublic,
  CampaignRunListResponse,
} from "@/types/campaign";

export interface ListCampaignsParams {
  limit?: number;
  offset?: number;
  includeArchived?: boolean;
}

export async function listCampaigns(params: ListCampaignsParams = {}): Promise<CampaignListResponse> {
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  if (params.includeArchived !== undefined) query.set("include_archived", String(params.includeArchived));
  const queryString = query.toString();
  return request<CampaignListResponse>(`/campaigns${queryString ? `?${queryString}` : ""}`, { method: "GET" });
}

export async function createCampaign(payload: CampaignCreateRequest): Promise<CampaignCreateResponse> {
  return request<CampaignCreateResponse>("/campaigns", { method: "POST", body: payload });
}

export async function getCampaign(campaignPublicId: string): Promise<CampaignPublic> {
  return request<CampaignPublic>(`/campaigns/${encodeURIComponent(campaignPublicId)}`, { method: "GET" });
}

export interface ListCampaignRunsParams {
  limit?: number;
  offset?: number;
}

export async function listCampaignRuns(campaignPublicId: string, params: ListCampaignRunsParams = {}): Promise<CampaignRunListResponse> {
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const queryString = query.toString();
  return request<CampaignRunListResponse>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/runs${queryString ? `?${queryString}` : ""}`,
    { method: "GET" },
  );
}
