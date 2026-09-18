// Typed service functions for the Governed Strategy Revision contract.
// Mirrors apps/api/app/orchestration/strategy_revision_router.py exactly
// (MVP-30B): POST /strategy/{base_strategy_id}/revision, GET
// /strategy/history.

import { request } from "@/lib/api/client";
import type { StrategyHistoryResponse, StrategyRevisionResult } from "@/types/strategy-revisions";

function campaignPath(campaignPublicId: string, suffix: string): string {
  return `/campaigns/${encodeURIComponent(campaignPublicId)}${suffix}`;
}

// One-shot governed command: authorizes and applies a complete new
// Strategy + Positioning state against the exact current base Strategy,
// consuming the exact named (unconsumed, APPROVED) StrategicApproval.
export async function reviseStrategy(
  campaignPublicId: string,
  baseStrategyPublicId: string,
  strategicApprovalId: string,
  summary: string,
  positioningStatement: string,
): Promise<StrategyRevisionResult> {
  return request<StrategyRevisionResult>(
    campaignPath(campaignPublicId, `/strategy/${encodeURIComponent(baseStrategyPublicId)}/revision`),
    { method: "POST", body: { strategic_approval_id: strategicApprovalId, summary, positioning_statement: positioningStatement } },
  );
}

export async function getStrategyHistory(campaignPublicId: string): Promise<StrategyHistoryResponse> {
  return request<StrategyHistoryResponse>(campaignPath(campaignPublicId, "/strategy/history"), { method: "GET" });
}
