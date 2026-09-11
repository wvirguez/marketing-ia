// Mirrors apps/api/app/campaigns/schemas.py exactly. No speculative
// fields, no internal UUID/workspace_id — only what the public API
// actually returns.

export interface CampaignPublic {
  id: string;
  name: string;
  status: string;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CampaignBriefPublic {
  id: string;
  campaign_id: string;
  version: number;
  prompt: string;
  product_type: string | null;
  price: string | null;
  audience: string | null;
  budget: string | null;
  channel: string | null;
  created_at: string;
}

export interface CampaignRunPublic {
  id: string;
  campaign_id: string;
  run_number: number;
  status: string;
  created_at: string;
}

export interface CampaignCreateRequest {
  name: string;
  prompt: string;
  product_type?: string | null;
  price?: string | null;
  audience?: string | null;
  budget?: string | null;
  channel?: string | null;
}

export interface CampaignCreateResponse {
  campaign: CampaignPublic;
  brief: CampaignBriefPublic;
  run: CampaignRunPublic;
}

export interface CampaignListResponse {
  items: CampaignPublic[];
  limit: number;
  offset: number;
  total: number;
}

export interface CampaignRunListResponse {
  items: CampaignRunPublic[];
  limit: number;
  offset: number;
  total: number;
}
