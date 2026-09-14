// Typed service functions for the Tracking contract. Mirrors
// apps/api/app/tracking/router.py exactly: GET (read), POST (create Plan
// / create Requirement, MVP-15B), and PATCH (Plan transition / Requirement
// status update, MVP-15B — previously wired backend-side but never called
// from the frontend until now).

import { request } from "@/lib/api/client";
import type { CreateTrackingRequirementRequest, TrackingPatchRequest, TrackingResponse } from "@/types/tracking";

export async function getTracking(campaignPublicId: string): Promise<TrackingResponse> {
  return request<TrackingResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/tracking`, {
    method: "GET",
  });
}

// No request body: every TrackingPlan field is either fixed (status is
// always created at NOT_DEFINED) or derived from the already-authorized
// campaign, so there is nothing for a caller to supply. `options.body` is
// left undefined here on purpose — the shared `request()` helper only
// attaches a body/Content-Type header when one is explicitly given.
export async function createTrackingPlan(campaignPublicId: string): Promise<TrackingResponse> {
  return request<TrackingResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/tracking`, {
    method: "POST",
  });
}

export async function createTrackingRequirement(campaignPublicId: string, name: string): Promise<TrackingResponse> {
  const body: CreateTrackingRequirementRequest = { name };
  return request<TrackingResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/tracking/requirements`, {
    method: "POST",
    body,
  });
}

export async function patchTracking(campaignPublicId: string, operation: TrackingPatchRequest): Promise<TrackingResponse> {
  return request<TrackingResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/tracking`, {
    method: "PATCH",
    body: operation,
  });
}
