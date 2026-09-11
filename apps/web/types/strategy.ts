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

export interface ExperimentPublic {
  id: string;
  hypothesis_id: string;
  description: string;
  status: string | null;
  created_at: string;
}

export interface StrategyPublic {
  id: string;
  campaign_id: string;
  version: number;
  summary: string;
  created_at: string;
}

export interface StrategyOutputResponse {
  strategy: StrategyPublic | null;
  positioning: PositioningPublic | null;
  hypotheses: HypothesisPublic[];
  experiments: ExperimentPublic[];
}
