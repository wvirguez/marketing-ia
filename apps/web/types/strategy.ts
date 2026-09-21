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
  // MVP-38: derived, never stored. `variant_count` is how many Variants pin
  // this version; `is_pinned` only means a governed pinning child exists
  // (a lock indicator — never a validity/readiness/causality claim).
  variant_count: number;
  is_pinned: boolean;
  // MVP-39: derived, never stored. `is_pinned` above is WIDENED — true
  // whenever EITHER a Variant OR a Measurement Contract exists.
  // `variant_count` keeps its exact MVP-38 meaning; a Contract never
  // increments it.
  has_measurement_contract: boolean;
  measurement_contract_version: number | null;
}

// MVP-38: the immutable identity of ONE declared condition, pinned to one
// immutable definition version. No role, weight, status, metric, result or
// validity field exists.
export interface VariantPublic {
  id: string;
  experiment_id: string;
  definition_version_id: string;
  ordinal: number;
  label: string;
  condition_description: string;
  created_at: string;
}

export interface VariantListResponse {
  experiment_id: string;
  items: VariantPublic[];
  limit: number;
  offset: number;
  total: number;
}

// MVP-38: `definition_version_id` is the EXPLICIT pin (the `EXD-…` id of the
// current tip) — the server never substitutes the current version.
export interface DeclareVariantRequest {
  definition_version_id: string;
  label: string;
  condition_description: string;
  client_request_id: string;
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

// MVP-39: an optional pre-execution expectation for one RequiredSignal.
// Never the same concept as a Learning EvidenceRelationship.
export type ExpectedDirection = "INCREASE" | "DECREASE" | "TARGET" | "NO_DIRECTION";

// MVP-39: one immutable RequiredSignal — a declared metric/observation a
// Measurement Contract requires. Not evidence itself: its existence never
// means evidence exists, is bound, or is sufficient.
export interface RequiredSignalPublic {
  id: string;
  ordinal: number;
  name: string;
  description: string;
  expected_direction: ExpectedDirection | null;
  evidence_requirement: string | null;
  tracking_required: boolean;
}

// MVP-39: the request shape for one declared RequiredSignal.
export interface RequiredSignalRequest {
  name: string;
  description: string;
  expected_direction: ExpectedDirection | null;
  evidence_requirement: string | null;
  tracking_required: boolean;
}

// MVP-39: one immutable Measurement Contract version — the PRE-EXECUTION
// declaration of how an Experiment's evidence is intended to be evaluated.
// Mirrors apps/api/app/strategy/schemas.py::MeasurementContractPublic. No
// status, frozen_at, execution_authorized, winner, or result field exists.
export interface MeasurementContractPublic {
  id: string;
  experiment_id: string;
  definition_version_id: string;
  version: number;
  measurement_window_days: number | null;
  minimum_evidence: string | null;
  success_criterion: string | null;
  analysis_method_intent: string | null;
  stopping_rule: string | null;
  decision_rule_intent: string | null;
  signals: RequiredSignalPublic[];
  created_at: string;
}

export interface MeasurementContractHistoryResponse {
  experiment_id: string;
  measurement_contract_label: string;
  current_version: number | null;
  versions: MeasurementContractPublic[];
}

// MVP-39: the FULL-STATE Contract version write payload. `definition_version_id`
// is the EXPLICIT pin (the current tip's `EXD-…` id) — the server never
// substitutes it. base_version is 0 for the first declaration, otherwise the
// current Contract tip version. At least one signal is required.
export interface DeclareMeasurementContractRequest {
  base_version: number;
  client_request_id: string;
  definition_version_id: string;
  measurement_window_days: number | null;
  minimum_evidence: string | null;
  success_criterion: string | null;
  analysis_method_intent: string | null;
  stopping_rule: string | null;
  decision_rule_intent: string | null;
  signals: RequiredSignalRequest[];
}

// MVP-40: mirrors apps/api/app/strategy/schemas.py::ExecutionAuthorizationPublic.
// An Execution Authorization asserts only that ONE specific, immutable
// configuration (the current Definition tip, the COMPLETE Variant set and the
// current Measurement Contract tip, plus a declared-only Execution
// Configuration) was authorized to begin future execution. There is
// deliberately no status/executing/assignment/exposure/tracking-valid/
// measurement-ready/result/winner/validity/causality field. `active` is the
// only derived state (revoked_at is null); signal counts are informational
// (a declared intent, never a claim that tracking exists or is valid).
export interface ExecutionAuthorizationVariantPublic {
  id: string;
  label: string;
  condition_description: string;
}

// Governed Execution Start: mirrors apps/api/app/strategy/schemas.py::ExecutionStartPublic.
// A HUMAN ATTESTATION that execution of one Authorization began — never verified
// external execution, assignment, delivery, exposure, evidence, a result or
// validity. `started_at` is the operator-attested instant; `created_at` is the
// server record time. Both are always shown and never interchangeable. There is
// deliberately no executing/completed/valid/successful field.
export interface ExecutionStartPublic {
  id: string;
  started_at: string;
  created_at: string;
}

export interface ExecutionAuthorizationPublic {
  id: string;
  experiment_id: string;
  definition_version_id: string;
  contract_version_id: string;
  unit_of_assignment: string;
  allocation_design: string;
  variants: ExecutionAuthorizationVariantPublic[];
  signal_count: number;
  tracking_required_signal_count: number;
  active: boolean;
  revoked_at: string | null;
  revoked_reason: string | null;
  superseded_by: string | null;
  // null until a human attests the start (Governed Execution Start).
  execution_start: ExecutionStartPublic | null;
  created_at: string;
}

export interface ExecutionAuthorizationHistoryResponse {
  experiment_id: string;
  current_id: string | null;
  authorizations: ExecutionAuthorizationPublic[];
}

// The client NEVER supplies the Definition, Contract or Variants — the server
// pins whatever is current under lock.
export interface AuthorizeExecutionRequest {
  client_request_id: string;
  unit_of_assignment: string;
  allocation_design: string;
}

export interface RevokeExecutionAuthorizationRequest {
  reason: string;
}

// Governed Execution Start: exactly the idempotency key and the operator-attested
// instant (an offset-aware ISO string). No assignment, unit, cohort, exposure,
// evidence, note or reference field exists.
export interface StartExecutionRequest {
  client_request_id: string;
  started_at: string;
}

// Experiment Evidence Binding: mirrors apps/api/app/strategy/schemas.py::EvidenceClaimPublic.
// An evidence claim is a human PROVENANCE CLAIM ONLY — a member asserted that ONE metric datum
// (metric entry + metric name) is associated with ONE required signal under ONE started execution
// attempt, at EXPERIMENT level. It is NOT eligibility, validation, currentness, sufficiency,
// correctness, tracking validity, assignment, exposure, Variant attribution, measurement, a result,
// a winner, attribution or causality — so no such field exists here.
// `later_correction_exists` is a read-time OBSERVATION (never stored, never a status).
export interface EvidenceClaimAuthorizationRef {
  id: string;
  revoked_at: string | null;
  revoked_reason: string | null;
}

export interface EvidenceClaimStartRef {
  id: string;
  started_at: string;
}

export interface EvidenceClaimSignalRef {
  id: string;
  name: string;
  expected_direction: string | null;
  tracking_required: boolean;
  contract_version_id: string;
  contract_version: number | null;
}

export interface EvidenceClaimDatum {
  metric_entry_id: string;
  metric_name: string;
  value: string | number | null;
  period_start: string | null;
  period_end: string | null;
  channel: string | null;
  source: string | null;
  entry_created_at: string | null;
}

export interface EvidenceClaimDistributionContext {
  distribution_id: string | null;
  evidence_id: string;
  source_reference: string | null;
  supersedes_evidence_id: string | null;
  superseded_by_evidence_id: string | null;
  correction_reason: string | null;
}

export interface EvidenceClaimPublic {
  id: string;
  experiment_id: string;
  semantics: string;
  scope: "EXPERIMENT_LEVEL";
  reporter_note: string;
  claimed_by: string | null;
  created_at: string;
  is_disposed: boolean;
  disposed_at: string | null;
  disposed_by: string | null;
  disposal_reason: string | null;
  authorization: EvidenceClaimAuthorizationRef;
  start: EvidenceClaimStartRef;
  required_signal: EvidenceClaimSignalRef | null;
  datum: EvidenceClaimDatum;
  distribution: EvidenceClaimDistributionContext | null;
  later_correction_exists: boolean;
  excluded_from_aggregate_and_analysis: boolean;
}

export interface EvidenceClaimListResponse {
  experiment_id: string;
  start_id: string;
  claims: EvidenceClaimPublic[];
}

// Exactly the four frozen inputs — no note, variant, eligibility, assignment, exposure or result.
export interface CreateEvidenceClaimRequest {
  client_request_id: string;
  required_signal_id: string;
  metric_entry_id: string;
  metric_name: string;
}

// Exactly a required reason — dispose carries no idempotency key.
export interface DisposeEvidenceClaimRequest {
  reason: string;
}
