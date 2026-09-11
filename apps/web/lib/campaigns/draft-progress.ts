// MVP-06B: frontend-only presentation derivation for the deterministic
// bootstrap's user-facing state (P1-1/P1-2 hardening). Never persisted,
// never sent to the backend, never a new API field, and never a change to
// CampaignRun.status itself — computed here purely from the existing
// `RunProgressPublic` (`GET .../progress`) the frontend already fetches.
// RUNNING != actively executing remains true globally
// (apps/api/app/campaigns/models.py::CampaignRunStatus's own docstring);
// this module only decides how to *present* that alongside the
// deterministic bootstrap's own stage-level truth, never how the backend
// stores or transitions it.

import type { BusinessStage, RunProgressPublic, StageExecutionPublic } from "@/types/orchestration";

// The exact business stages this MVP's deterministic bootstrap executes, in
// order (mirrors apps/api/app/orchestration/service.py's own
// `_BOOTSTRAP_STAGE_ORDER`). CREATIVE and everything after intentionally
// stays PENDING and is out of scope for this derivation (MVP-05E/05F).
const BOOTSTRAP_STAGES: readonly BusinessStage[] = ["RESEARCH", "AUDIENCE", "STRATEGY", "PLAN", "CONTENT"];

const BUSINESS_STAGE_LABELS: Partial<Record<BusinessStage, string>> = {
  RESEARCH: "Investigación",
  AUDIENCE: "Audiencia",
  STRATEGY: "Estrategia",
  PLAN: "Planificación",
  CONTENT: "Contenido",
};

/** Business-language label only — never an AGENT-0N identifier or any
 * internal orchestration term. */
export function businessStageLabel(stage: BusinessStage): string {
  return BUSINESS_STAGE_LABELS[stage] ?? stage;
}

function bootstrapStages(progress: RunProgressPublic): StageExecutionPublic[] {
  return progress.stages.filter((stage) => BOOTSTRAP_STAGES.includes(stage.stage));
}

/** The first bootstrap-sequence stage (RESEARCH->CONTENT order) that is
 * FAILED, or null if none is — never inferred from CampaignRun.status. */
export function firstFailedBootstrapStage(progress: RunProgressPublic): StageExecutionPublic | null {
  return bootstrapStages(progress).find((stage) => stage.status === "FAILED") ?? null;
}

/** True only once CONTENT itself has COMPLETED and no bootstrap stage
 * failed — never CREATIVE, never CampaignRun.status, never "every stage
 * complete" (CREATIVE onward is intentionally PENDING for this MVP). */
export function isDraftGenerated(progress: RunProgressPublic): boolean {
  if (firstFailedBootstrapStage(progress) !== null) return false;
  return progress.stages.some((stage) => stage.stage === "CONTENT" && stage.status === "COMPLETED");
}

export type DraftPresentationState = "NOT_STARTED" | "GENERATING" | "DRAFT_GENERATED" | "FAILED";

/** Frontend-only presentation state for the MVP deterministic bootstrap —
 * never persisted, never derived from CampaignRun.status alone. A run that
 * is technically still `RUNNING` at the backend is presented as
 * `DRAFT_GENERATED` here once CONTENT has actually completed; RUNNING
 * itself is never redefined anywhere else. */
export function getDraftPresentationState(progress: RunProgressPublic): DraftPresentationState {
  if (progress.run_status === "CREATED") return "NOT_STARTED";
  if (firstFailedBootstrapStage(progress) !== null) return "FAILED";
  if (isDraftGenerated(progress)) return "DRAFT_GENERATED";
  return "GENERATING";
}
