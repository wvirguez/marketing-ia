// Mirrors apps/api/app/planning/schemas.py exactly. No internal UUID, no
// workspace_id — only public_id-derived fields. No field here (or on the
// backend) represents approval, readiness, or production authorization.

export interface PlanItemPublic {
  id: string;
  format: string;
  objective: string;
  sequence: number;
  scheduled_date: string | null;
  created_at: string;
}

export interface ContentPlanPublic {
  id: string;
  campaign_id: string;
  version: number;
  summary: string;
  created_at: string;
}

export interface PlanOutputResponse {
  plan: ContentPlanPublic | null;
  items: PlanItemPublic[];
}
