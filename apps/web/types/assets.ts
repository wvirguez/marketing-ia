// Mirrors apps/api/app/assets/schemas.py exactly. No internal UUID, no
// workspace_id, no content_piece_id/creative_brief_id linkage field — only
// what the backend's frozen GET-only response actually exposes. No field
// here (or on the backend) represents approval authority, production
// authorization, or distribution readiness.

export interface CreativeBriefPublic {
  spec: Record<string, unknown>;
}

export interface AssetVersionPublic {
  storage_reference: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AssetPublic {
  id: string;
  kind: string;
  status: string | null;
  current_version: AssetVersionPublic | null;
}

export interface AssetsForContentPieceResponse {
  creative_brief: CreativeBriefPublic | null;
  assets: AssetPublic[];
}
