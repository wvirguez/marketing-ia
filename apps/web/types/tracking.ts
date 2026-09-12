// Mirrors apps/api/app/tracking/schemas.py exactly. No internal UUID, no
// workspace_id, no campaign_id linkage field — only what the backend's
// GET-only response actually exposes. `status` on the Plan is the sole
// physical readiness field (BACKEND-15 TRK-D02/TRK-D03) — a manually
// updated, self-reported workflow marker, never an automated technical
// verification result. No provider, pixel, credential, or event-schema
// field exists here or on the backend.

export type TrackingReadinessStatus =
  | "NOT_DEFINED"
  | "REQUIREMENTS_DEFINED"
  | "CONFIGURATION_PENDING"
  | "CONFIGURED"
  | "VERIFICATION_PENDING"
  | "FAILED_VERIFICATION"
  | "CERTIFIED";

export interface TrackingRequirementPublic {
  id: string;
  name: string;
  status: string | null;
}

export interface TrackingPlanPublic {
  id: string;
  status: TrackingReadinessStatus;
  requirements: TrackingRequirementPublic[];
}

export interface TrackingResponse {
  plan: TrackingPlanPublic | null;
}
