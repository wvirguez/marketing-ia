// MVP-28B: StrategicDecision governance (frozen MVP-28A/-R1/-R2 contract).
// A durable, higher-order governance record that a specific accepted
// StrategicRecommendationCandidate's proposed campaign-direction change has
// been ADOPTed, DEFERred, or DECLINEd — never itself a Strategy mutation, a
// StrategicApproval, or an execution authorization. "current" is derived
// server-side (superseded_at IS NULL), never a second persisted status.
// Recording (POST) and superseding (POST .../supersede) are distinct, never-
// conflated actions, exactly mirroring Commercial's own Objective/Offer
// convention — replacement is never implemented by editing an existing row
// in place.

export type StrategicDecisionType = "ADOPT" | "DEFER" | "DECLINE";

export interface StrategicDecisionPublic {
  id: string;
  campaign_id: string;
  // Nullable only because the schema itself reserves room for a future,
  // separately-authorized alternate origin (a Gate Decision) — every row
  // created through this MVP's own write path always has one.
  strategic_recommendation_candidate_id: string | null;
  decision_type: StrategicDecisionType;
  statement: string;
  created_at: string;
  current: boolean;
  superseded_at: string | null;
  superseded_by_strategic_decision_id: string | null;
}
