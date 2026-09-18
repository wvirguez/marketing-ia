// Typed service functions for the Strategy contract. GET mirrors MVP-05C;
// the create-Hypothesis POST mirrors apps/api/app/strategy/router.py
// exactly (MVP-31A/-31A-R1/MVP-31B) — the only write route this module
// exposes.

import { request } from "@/lib/api/client";
import type { CreateHypothesisRequest, HypothesisPublic, StrategyOutputResponse } from "@/types/strategy";

export async function getStrategy(campaignPublicId: string): Promise<StrategyOutputResponse> {
  return request<StrategyOutputResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/strategy`, {
    method: "GET",
  });
}

export async function createHypothesis(
  campaignPublicId: string,
  strategyPublicId: string,
  statement: string,
): Promise<HypothesisPublic> {
  const payload: CreateHypothesisRequest = { statement };
  return request<HypothesisPublic>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/strategy/${encodeURIComponent(strategyPublicId)}/hypotheses`,
    { method: "POST", body: payload },
  );
}
