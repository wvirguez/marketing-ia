// Mirrors apps/api/app/orchestration/schemas.py exactly. No internal
// UUID, no workspace_id, no AGENT-0N identifier — only public_id-derived
// fields and the business-language stage vocabulary the backend itself
// exposes (app/orchestration/models.py::BusinessStage).

export type BusinessStage =
  | "RESEARCH"
  | "AUDIENCE"
  | "STRATEGY"
  | "PLAN"
  | "CONTENT"
  | "CREATIVE"
  | "DISTRIBUTION"
  | "PAID_MEDIA"
  | "TRACKING"
  | "MEASUREMENT"
  | "LEARNING";

export type StageExecutionStatus =
  | "PENDING"
  | "READY"
  | "RUNNING"
  | "WAITING_FOR_INPUT"
  | "BLOCKED"
  | "COMPLETED"
  | "FAILED"
  | "SKIPPED"
  | "CANCELLED";

export interface StageExecutionPublic {
  id: string;
  stage: BusinessStage;
  ordinal: number;
  status: StageExecutionStatus;
  started_at: string | null;
  completed_at: string | null;
  blocked_reason: string | null;
  failure_reason: string | null;
}

export interface StageExecutionListResponse {
  items: StageExecutionPublic[];
}

export interface RunProgressPublic {
  campaign_id: string;
  run_id: string;
  run_status: string;
  current_stage: BusinessStage | null;
  stages: StageExecutionPublic[];
  waiting_for_input: boolean;
  open_decision_count: number;
}
