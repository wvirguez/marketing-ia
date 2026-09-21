// Typed service functions for the Strategy contract. GET mirrors MVP-05C;
// the create-Hypothesis and create-Experiment POSTs mirror
// apps/api/app/strategy/router.py exactly (MVP-31A/-31A-R1/MVP-31B,
// MVP-32A/-32A-R1/MVP-32B) and the MVP-37 Experiment Definition version
// write — the only write routes this module exposes.

import { request } from "@/lib/api/client";
import type {
  AuthorizeExecutionRequest,
  CreateExperimentRequest,
  CreateHypothesisRequest,
  DeclareExperimentDefinitionRequest,
  DeclareMeasurementContractRequest,
  DeclareVariantRequest,
  ExecutionAuthorizationHistoryResponse,
  ExecutionAuthorizationPublic,
  ExperimentDefinitionPublic,
  ExperimentPublic,
  HypothesisPublic,
  MeasurementContractHistoryResponse,
  MeasurementContractPublic,
  RevokeExecutionAuthorizationRequest,
  StartExecutionRequest,
  StrategyOutputResponse,
  VariantListResponse,
  VariantPublic,
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

// MVP-38: declares ONE immutable Variant (the identity of one declared
// condition) pinned to the EXPLICITLY named current definition version.
// Idempotent on client_request_id (201 new / 200 replay). Declaring a Variant
// fixes the definition version; it is NOT allocation, exposure, measurement,
// a result or execution authorization, and it cannot currently be corrected.
export async function declareVariant(
  campaignPublicId: string,
  experimentPublicId: string,
  payload: DeclareVariantRequest,
): Promise<VariantPublic> {
  return request<VariantPublic>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/experiments/${encodeURIComponent(experimentPublicId)}/variants`,
    { method: "POST", body: payload },
  );
}

// MVP-38: a deterministic page of the Experiment's declared Variants
// (ordered by pinned version, then ordinal). limit <= 100.
export async function listVariants(
  campaignPublicId: string,
  experimentPublicId: string,
  page: { limit: number; offset: number },
): Promise<VariantListResponse> {
  const query = `?limit=${encodeURIComponent(String(page.limit))}&offset=${encodeURIComponent(String(page.offset))}`;
  return request<VariantListResponse>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/experiments/${encodeURIComponent(experimentPublicId)}/variants${query}`,
    { method: "GET" },
  );
}

// MVP-39: declares (base_version 0) or revises (base_version = current
// Contract tip) an Experiment's PRE-EXECUTION Measurement Contract. One
// route, full-state, append-only, idempotent on client_request_id (201 new
// / 200 replay). The explicit tip pin (definition_version_id) is never
// silently substituted. Writing is not freezing and not execution
// authorization — there is no freeze endpoint in this domain.
export async function declareMeasurementContract(
  campaignPublicId: string,
  experimentPublicId: string,
  payload: DeclareMeasurementContractRequest,
): Promise<MeasurementContractPublic> {
  return request<MeasurementContractPublic>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/experiments/${encodeURIComponent(experimentPublicId)}/measurement-contract`,
    { method: "POST", body: payload },
  );
}

// MVP-39: the current Measurement Contract tip, or null if none declared.
export async function getMeasurementContract(
  campaignPublicId: string,
  experimentPublicId: string,
): Promise<MeasurementContractPublic | null> {
  return request<MeasurementContractPublic | null>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/experiments/${encodeURIComponent(experimentPublicId)}/measurement-contract`,
    { method: "GET" },
  );
}

// MVP-39: the unpaginated ascending version history.
export async function getMeasurementContractHistory(
  campaignPublicId: string,
  experimentPublicId: string,
): Promise<MeasurementContractHistoryResponse> {
  return request<MeasurementContractHistoryResponse>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/experiments/${encodeURIComponent(experimentPublicId)}/measurement-contract/history`,
    { method: "GET" },
  );
}

// MVP-40: Execution Authorization. The client never names the Definition,
// Contract or Variants — the server pins whatever is current.
export async function authorizeExecution(
  campaignPublicId: string,
  experimentPublicId: string,
  payload: AuthorizeExecutionRequest,
): Promise<ExecutionAuthorizationPublic> {
  return request<ExecutionAuthorizationPublic>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/experiments/${encodeURIComponent(experimentPublicId)}/execution-authorization`,
    { method: "POST", body: payload },
  );
}

// MVP-40: the current ACTIVE Authorization, or null.
export async function getExecutionAuthorization(
  campaignPublicId: string,
  experimentPublicId: string,
): Promise<ExecutionAuthorizationPublic | null> {
  return request<ExecutionAuthorizationPublic | null>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/experiments/${encodeURIComponent(experimentPublicId)}/execution-authorization`,
    { method: "GET" },
  );
}

// MVP-40: the unpaginated ascending history (active and revoked alike).
export async function getExecutionAuthorizationHistory(
  campaignPublicId: string,
  experimentPublicId: string,
): Promise<ExecutionAuthorizationHistoryResponse> {
  return request<ExecutionAuthorizationHistoryResponse>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/experiments/${encodeURIComponent(experimentPublicId)}/execution-authorization/history`,
    { method: "GET" },
  );
}

// MVP-40: revokes the current ACTIVE Authorization (one-way; reason required).
export async function revokeExecutionAuthorization(
  campaignPublicId: string,
  experimentPublicId: string,
  payload: RevokeExecutionAuthorizationRequest,
): Promise<ExecutionAuthorizationPublic> {
  return request<ExecutionAuthorizationPublic>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/experiments/${encodeURIComponent(experimentPublicId)}/execution-authorization/revoke`,
    { method: "POST", body: payload },
  );
}

// Governed Execution Start: a human ATTESTS that execution of ONE explicitly named
// Authorization began at `started_at`. Idempotent on `client_request_id`. The
// attestation is never verified against the outside world.
export async function startExecution(
  campaignPublicId: string,
  experimentPublicId: string,
  authorizationPublicId: string,
  payload: StartExecutionRequest,
): Promise<ExecutionAuthorizationPublic> {
  return request<ExecutionAuthorizationPublic>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/experiments/${encodeURIComponent(experimentPublicId)}/execution-authorizations/${encodeURIComponent(authorizationPublicId)}/start`,
    { method: "POST", body: payload },
  );
}
