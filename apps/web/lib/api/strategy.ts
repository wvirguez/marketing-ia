// Typed service functions for the Strategy contract. GET mirrors MVP-05C;
// the create-Hypothesis and create-Experiment POSTs mirror
// apps/api/app/strategy/router.py exactly (MVP-31A/-31A-R1/MVP-31B,
// MVP-32A/-32A-R1/MVP-32B) and the MVP-37 Experiment Definition version
// write — the only write routes this module exposes.

import { request } from "@/lib/api/client";
import type {
  CreateExperimentRequest,
  CreateHypothesisRequest,
  DeclareExperimentDefinitionRequest,
  ExperimentDefinitionPublic,
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

// MVP-37: declares (base_version 0) or revises (base_version = current tip)
// an Experiment's comparison Definition. One route, full-state, append-only,
// idempotent on client_request_id (201 new / 200 replay). Writing is neither
// approval nor execution authorization. No history client exists yet — the
// history route is API-only in MVP-37.
export async function declareExperimentDefinition(
  campaignPublicId: string,
  experimentPublicId: string,
  payload: DeclareExperimentDefinitionRequest,
): Promise<ExperimentDefinitionPublic> {
  return request<ExperimentDefinitionPublic>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/experiments/${encodeURIComponent(experimentPublicId)}/definition-versions`,
    { method: "POST", body: payload },
  );
}
