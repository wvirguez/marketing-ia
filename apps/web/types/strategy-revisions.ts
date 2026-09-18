// MVP-30B: Governed Strategy Revision (frozen MVP-30A/-30A-R1 contract).
// Mirrors apps/api/app/orchestration/strategy_revision_schemas.py exactly.
// A durable governance-provenance record connecting one consumed
// StrategicApproval to the specific Strategy version it authorized — never
// itself Execution Authorization or Distribution Readiness.

import type { PositioningPublic, StrategyPublic } from "@/types/strategy";

export interface StrategyRevisionPublic {
  id: string;
  campaign_id: string;
  strategic_approval_id: string;
  base_strategy_id: string;
  result_strategy_id: string;
  created_at: string;
}

export interface StrategyRevisionResult {
  strategy: StrategyPublic;
  positioning: PositioningPublic;
  revision: StrategyRevisionPublic;
}

export interface StrategyHistoryItem {
  strategy: StrategyPublic;
  // null for BOOTSTRAP-origin (legacy) Strategy versions — never
  // fabricated (MVP-30A-R1 §AG: no synthetic historical governance
  // records).
  revision: StrategyRevisionPublic | null;
}

export interface StrategyHistoryResponse {
  items: StrategyHistoryItem[];
}
