// Mirrors apps/api/app/content/schemas.py exactly. No internal UUID, no
// workspace_id — only public_id-derived fields. No field here (or on the
// backend) represents production authorization, brief contents, version
// history, or an asset. MVP-18B adds the Distribution read model.
//
// MVP-17B: `ContentApprovalPublic`/`latest_approval` are additive — the
// frozen field set is exactly `id`/`status`/`decided_at` (MVP-17A §K).
// `reviewer_user_id` is deliberately never exposed here (actor
// attribution lives in the Audit Event trail, not this read model).

export type ContentPieceStatus =
  | "DRAFT"
  | "IN_PRODUCTION"
  | "PRODUCED"
  | "READY_FOR_REVIEW"
  | "REVISION_REQUESTED"
  | "APPROVED"
  | "READY_FOR_DISTRIBUTION"
  | "DISTRIBUTED"
  | "ARCHIVED";

export type ContentApprovalStatus = "REQUESTED" | "UNDER_REVIEW" | "APPROVED" | "CHANGES_REQUESTED" | "REJECTED" | "EXPIRED";

// The only three decisions the backend's `.../decision` route accepts
// from a client (MVP-17B §24) — EXPIRED/REQUESTED/UNDER_REVIEW are never
// valid request values.
export type ContentApprovalDecision = "APPROVED" | "CHANGES_REQUESTED" | "REJECTED";

export interface ContentPiecePublic {
  id: string;
  format: string;
  objective: string;
  funnel_stage: string;
  cta: string;
  channel: string;
  status: ContentPieceStatus;
  archived_at: string | null;
  created_at: string;
}

export interface ContentVersionPublic {
  id: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ContentApprovalPublic {
  id: string;
  status: ContentApprovalStatus;
  decided_at: string | null;
}

export interface ContentPieceListResponse {
  items: ContentPiecePublic[];
}

// MVP-35A/-35B: the client supplies only the Piece's own fields plus the
// mandatory initial ContentVersion payload (a bare Piece has no legal
// route to ever acquire its first Version — MVP-35A §O). No
// content_brief_id/workspace_id/campaign_id/experiment_id/variant_id/
// actor_user_id/status is ever accepted — all are route-derived or do
// not exist on this entity.
export interface CreateContentPieceRequest {
  format: string;
  objective: string;
  funnel_stage: string;
  cta: string;
  channel: string;
  payload: Record<string, unknown>;
}

export interface ContentPieceDetailResponse {
  piece: ContentPiecePublic;
  latest_version: ContentVersionPublic | null;
  latest_approval: ContentApprovalPublic | null;
  distribution: DistributionPublic | null;
}

export interface DistributionPublic {
  id: string;
  status: "READY" | "DISTRIBUTED";
  channel: string;
  external_reference: string | null;
  ready_at: string;
  distributed_at: string | null;
  // MVP-24: identity-only (MVP-24A-R1) — public IDs of the
  // TrackingRequirements declared applicable to this Distribution. Never
  // a copy of TrackingRequirement.name/status — those remain
  // current-state-only, reachable only via a fresh GET /tracking.
  tracking_requirement_ids: string[];
}

export interface TrackingRequirementAssociationRequest {
  tracking_requirement_id: string;
}

export interface RecordApprovalDecisionRequest {
  decision: ContentApprovalDecision;
}

// MVP-20: legal only while piece.status === "REVISION_REQUESTED" — the
// only client-controlled field is the immutable version content itself;
// the server derives workspace/piece linkage, public id, created_at, and
// the coupled REVISION_REQUESTED -> IN_PRODUCTION transition.
export interface CreateContentVersionRequest {
  payload: Record<string, unknown>;
}

// MVP-19B: Distribution-linked Measurement Evidence. CORE SEMANTIC — the
// system may only ever say "the user reported these metrics for this
// Distribution," never that the Distribution generated/caused them. No
// attribution field exists here, or anywhere in this contract.
export interface DistributionEvidencePublic {
  id: string;
  distribution_id: string;
  content_piece_id: string;
  metric_entry_id: string;
  evidence_scope: "DISTRIBUTION_SPECIFIC";
  values: Record<string, string>;
  period_start: string;
  period_end: string;
  channel: string;
  source: "MANUAL";
  source_reference: string | null;
  reported_by: string | null;
  reported_at: string;
  is_current: boolean;
  supersedes_evidence_id: string | null;
  correction_reason: string | null;
}

export interface DistributionEvidenceListResponse {
  items: DistributionEvidencePublic[];
  limit: number;
  offset: number;
  total: number;
}

export interface RecordDistributionEvidenceRequest {
  period_start: string;
  period_end: string;
  values: Record<string, string>;
  client_request_id: string;
  source_reference?: string | null;
}

export interface RecordDistributionEvidenceCorrectionRequest extends RecordDistributionEvidenceRequest {
  correction_reason: string;
}

// MVP-21: read-only, descriptive, non-causal per-Distribution summary over
// current (non-superseded) Evidence only. No sum/average/min/max/trend/
// percent-change field exists here or on the backend (MVP-21A §I-M) — only
// cardinality and pure selection-by-reporting-chronology are computed.
export interface MetricSummaryItem {
  metric_name: string;
  report_count: number;
  latest_value: string;
  latest_period_start: string;
  latest_period_end: string;
  latest_reported_at: string;
  earliest_value: string;
  earliest_reported_at: string;
}

export interface DistributionEvidenceSummaryPublic {
  content_piece_id: string;
  distribution_id: string | null;
  content_version_id: string | null;
  channel: string | null;
  metrics: MetricSummaryItem[];
}
