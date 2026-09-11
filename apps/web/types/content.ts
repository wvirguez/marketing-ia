// Mirrors apps/api/app/content/schemas.py exactly. No internal UUID, no
// workspace_id — only public_id-derived fields. No field here (or on the
// backend) represents approval authority, production authorization,
// distribution readiness, brief contents, version history, or an asset.

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

export interface ContentPieceListResponse {
  items: ContentPiecePublic[];
}

export interface ContentPieceDetailResponse {
  piece: ContentPiecePublic;
  latest_version: ContentVersionPublic | null;
}
