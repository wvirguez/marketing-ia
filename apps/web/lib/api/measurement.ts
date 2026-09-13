// Typed service functions for the Measurement contract. Mirrors
// apps/api/app/measurement/router.py's GET/POST /metrics (MVP-09B) and
// GET /analysis + POST /analysis/run (MVP-11D-B). PUT /metrics
// (append-correction) is still deliberately unused — no correction UI
// exists yet.

import { request } from "@/lib/api/client";
import type {
  AnalysisResponse,
  MeasurementAnalysisRunPublic,
  MeasurementAnalysisRunTriggerRequest,
  MetricEntryListResponse,
  MetricEntryPublic,
  MetricEntryWriteRequest,
} from "@/types/measurement";

export async function getMetrics(campaignPublicId: string): Promise<MetricEntryListResponse> {
  return request<MetricEntryListResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/metrics`, {
    method: "GET",
  });
}

export async function createMetricEntry(
  campaignPublicId: string,
  payload: MetricEntryWriteRequest,
): Promise<MetricEntryPublic> {
  return request<MetricEntryPublic>(`/campaigns/${encodeURIComponent(campaignPublicId)}/metrics`, {
    method: "POST",
    body: payload,
  });
}

export async function getCampaignAnalysis(campaignPublicId: string): Promise<AnalysisResponse> {
  return request<AnalysisResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/analysis`, {
    method: "GET",
  });
}

export async function triggerCampaignAnalysis(
  campaignPublicId: string,
  payload: MeasurementAnalysisRunTriggerRequest,
): Promise<MeasurementAnalysisRunPublic> {
  return request<MeasurementAnalysisRunPublic>(`/campaigns/${encodeURIComponent(campaignPublicId)}/analysis/run`, {
    method: "POST",
    body: payload,
  });
}
