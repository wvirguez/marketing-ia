// Typed service functions for the Measurement contract consumed by
// MVP-09B. Mirrors apps/api/app/measurement/router.py's GET/POST /metrics
// routes only — the backend also exposes PUT /metrics (append-correction)
// and GET /analysis, but this first slice deliberately never calls
// either: no correction UI, no Observation/Signal/AnalysisResult read.

import { request } from "@/lib/api/client";
import type { MetricEntryListResponse, MetricEntryPublic, MetricEntryWriteRequest } from "@/types/measurement";

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
