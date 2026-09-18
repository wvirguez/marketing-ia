// Mirrors apps/api/app/strategy/schemas.py exactly. No internal UUID, no
// workspace_id — only public_id-derived fields. No field here (or on the
// backend) represents approval, maturity, or a Strategic Decision.

export type HypothesisStatus = "OPEN" | "CONFIRMED" | "REFUTED";

export interface PositioningPublic {
  id: string;
  statement: string;
  created_at: string;
}

export interface HypothesisPublic {
  id: string;
  statement: string;
  status: HypothesisStatus;
  created_at: string;
}

// MVP-31A/-31A-R1: the complete governed create payload — statement only.
// No status/strategy_id/workspace_id/campaign_id/origin is ever sent —
// all server-derived or server-controlled (status always starts OPEN).
export interface CreateHypothesisRequest {
  statement: string;
}

export interface ExperimentPublic {
  id: string;
  hypothesis_id: string;
  description: string;
  status: string | null;
  created_at: string;
}

// MVP-30B: "BOOTSTRAP" (deterministic initial draft) or "REVISION" (a
// governed Strategy Revision, MVP-30A/-30A-R1) — never a third value.
export type StrategyOrigin = "BOOTSTRAP" | "REVISION";

export interface StrategyPublic {
  id: string;
  campaign_id: string;
  version: number;
  origin: StrategyOrigin;
  summary: string;
  created_at: string;
}

export interface StrategyOutputResponse {
  strategy: StrategyPublic | null;
  positioning: PositioningPublic | null;
  hypotheses: HypothesisPublic[];
  experiments: ExperimentPublic[];
}
