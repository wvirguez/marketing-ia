// Mirrors apps/api/app/planning/schemas.py exactly. No internal UUID, no
// workspace_id — only public_id-derived fields. No field here (or on the
// backend) represents approval, readiness, or production authorization.

// Mirrors apps/api/app/content/schemas.py::ContentBriefPublic (MVP-34A/-34B).
// No origin/experiment_id/workspace_id/actor field — none is part of the
// frozen contract (actor attribution lives only in the Audit Event trail).
export interface ContentBriefPublic {
  id: string;
  plan_item_id: string;
  content_plan_id: string;
  brief: string;
  created_at: string;
}

export interface PlanItemPublic {
  id: string;
  format: string;
  objective: string;
  sequence: number;
  scheduled_date: string | null;
  created_at: string;
  // MVP-34A §Q/MVP-34B: null = not yet briefed. Only reflects the CURRENT
  // ContentPlan's items — a Brief on a historical PlanItem remains
  // backend-createable but is not exposed by GET /plan (MVP34A-OBS-1).
  brief: ContentBriefPublic | null;
}

// MVP-34A §K/MVP-34B: the client supplies only the free-text Brief —
// plan_item_id/content_plan_id/workspace_id/experiment_id/actor_user_id
// are all route-derived or nonexistent on this entity, never accepted.
export interface CreateContentBriefRequest {
  brief: string;
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
