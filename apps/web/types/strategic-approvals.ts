// MVP-29B: StrategicApproval governance (frozen MVP-29A contract). A
// durable, strictly insert-only governance record that one specific,
// currently-ADOPT StrategicDecision has separately received (or been
// denied) the ratification required to become eligible for a future,
// not-yet-implemented StrategyRevision workflow. Never itself a Strategy
// mutation. Unlike StrategicDecisionPublic, there is no "current"/
// "superseded_at" pair here at all — an Approval is one-shot and never
// reopened or replaced (MVP-29A §G/§J/§K).

export type StrategicApprovalOutcome = "APPROVED" | "REJECTED";

export interface StrategicApprovalPublic {
  id: string;
  campaign_id: string;
  strategic_decision_id: string;
  outcome: StrategicApprovalOutcome;
  created_at: string;
}
