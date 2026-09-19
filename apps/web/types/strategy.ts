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

// MVP-37: declared comparison intent only. "CONTROLLED" is a declared
// design intent — never a validated experimental status.
export type ComparisonType = "OBSERVATIONAL" | "CONTROLLED";

// MVP-37: one immutable Definition version. Mirrors
// apps/api/app/strategy/schemas.py::ExperimentDefinitionPublic. No field
// here represents validity, causality, a result, a winner, a measurement
// contract, or execution authorization. `non_conclusion_codes` is derived,
// response-only metadata.
export interface ExperimentDefinitionPublic {
  id: string;
  experiment_id: string;
  version: number;
  comparison_question: string;
  comparison_type: ComparisonType;
  changed_factor: string;
  controlled_factors: string[];
  comparison_basis: string;
  scope: string;
  learning_intent: string;
  non_conclusion_boundary: string;
  non_conclusion_codes: string[];
  created_at: string;
}

// MVP-37: additive — `definition` is the current tip (null when none is
// declared) and `comparison_label` is a derived, declaration-only label
// (NO_COMPARISON_DECLARED / DECLARED_OBSERVATIONAL_INTENT /
// DECLARED_CONTROLLED_INTENT).
export interface ExperimentPublic {
  id: string;
  hypothesis_id: string;
  description: string;
  status: string | null;
  created_at: string;
  comparison_label: string;
  definition: ExperimentDefinitionPublic | null;
}

// MVP-37: the FULL-STATE version-write payload. base_version is 0 for the
// first declaration, otherwise the current tip version.
export interface DeclareExperimentDefinitionRequest {
  base_version: number;
  client_request_id: string;
  comparison_question: string;
  comparison_type: ComparisonType;
  changed_factor: string;
  controlled_factors: string[];
  comparison_basis: string;
  scope: string;
  learning_intent: string;
  non_conclusion_boundary: string;
}

// MVP-32A/-32A-R1: the complete governed create payload — description
// only. No hypothesis_id/strategy_id/workspace_id/campaign_id/status/
// actor is ever sent — all server-derived or server-controlled (status
// always starts "RECORDED").
export interface CreateExperimentRequest {
  description: string;
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
