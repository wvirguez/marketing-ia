// Typed service functions for the Strategy contract. GET mirrors MVP-05C;
// the create-Hypothesis and create-Experiment POSTs mirror
// apps/api/app/strategy/router.py exactly (MVP-31A/-31A-R1/MVP-31B,
// MVP-32A/-32A-R1/MVP-32B) — the only write routes this module exposes.

import { request } from "@/lib/api/client";
import type {
  CreateExperimentRequest,
  CreateHypothesisRequest,
  ExperimentPublic,
  HypothesisPublic,
  StrategyOutputResponse,
} from "@/types/strategy";

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

// No strategy_id anywhere here — Strategy currency is a server-side
// eligibility check derived from the named Hypothesis's own ancestry
// (MVP-32A-R1 §14), never a client-supplied identifier.
export async function createExperiment(
  campaignPublicId: string,
  hypothesisPublicId: string,
  description: string,
): Promise<ExperimentPublic> {
  const payload: CreateExperimentRequest = { description };
  return request<ExperimentPublic>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/hypotheses/${encodeURIComponent(hypothesisPublicId)}/experiments`,
    { method: "POST", body: payload },
  );
}
