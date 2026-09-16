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
  // MVP-24: identity-only (MVP-24A-R1) — public IDs of the
  // ContentDistributions this Requirement is declared applicable to.
  // Never implies verification, firing, or attribution.
  associated_distribution_ids: string[];
}

export interface TrackingPlanPublic {
  id: string;
  status: TrackingReadinessStatus;
  requirements: TrackingRequirementPublic[];
}

export interface TrackingResponse {
  plan: TrackingPlanPublic | null;
}

// Request-side DTOs (MVP-15B). Plan creation takes no body — every field
// is either fixed or derived from the already-authorized campaign — so no
// request type exists for it.

export interface CreateTrackingRequirementRequest {
  name: string;
}

export interface TrackingTransitionPlanRequest {
  operation: "TRANSITION_PLAN";
  target_status: TrackingReadinessStatus;
}

export interface TrackingUpdateRequirementStatusRequest {
  operation: "UPDATE_REQUIREMENT_STATUS";
  requirement_id: string;
  status: string | null;
}

export type TrackingPatchRequest = TrackingTransitionPlanRequest | TrackingUpdateRequirementStatusRequest;
