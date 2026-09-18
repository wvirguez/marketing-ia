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
  // MVP-33B: nullable Experiment provenance, exposed as the Experiment's
  // public id. null = generic plan (Case G); a non-null value makes
  // exactly one claim: "this plan was created to operationalize that
  // Experiment" — nothing about Variant, execution, or measurement.
  experiment_id: string | null;
  version: number;
  summary: string;
  created_at: string;
}

export interface PlanOutputResponse {
  plan: ContentPlanPublic | null;
  items: PlanItemPublic[];
}

// MVP-33B: server-controlled fields (origin, version, campaign_run_id,
// stage_execution_id, status, actor) are never part of this shape.
export interface CreateContentPlanRequest {
  summary: string;
  experiment_public_id?: string | null;
}
