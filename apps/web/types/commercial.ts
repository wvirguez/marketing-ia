// MVP-27: Commercial Objective / Offer governance. 0..N simultaneously
// current per Campaign for both entities — no PRIMARY, no version, no
// target field. "current" is derived server-side (superseded_at IS NULL),
// never a second persisted status. Independent create (POST) and
// supersede (POST .../supersede) are distinct, never-conflated actions.

export interface CommercialObjectivePublic {
  id: string;
  campaign_id: string;
  statement: string;
  created_at: string;
  current: boolean;
  superseded_at: string | null;
  superseded_by_commercial_objective_id: string | null;
}

export interface OfferPublic {
  id: string;
  campaign_id: string;
  statement: string;
  // NULL+NULL = unknown/undetermined (including variable) pricing;
  // both non-null = a known price, possibly "0" (genuinely free).
  price: string | null;
  currency: string | null;
  created_at: string;
  current: boolean;
  superseded_at: string | null;
  superseded_by_offer_id: string | null;
}

// MVP-36 (frozen by MVP-36A/-R1): governed CommercialOutcome — EVENT
// semantics only (one row = one realized business event, never a period
// rollup). `content_distribution_id` means ONLY observational
// association, never attribution/causality — no field here, or ever
// under this frozen contract, implies the Distribution caused, generated,
// or is responsible for the outcome. No experiment_id/variant_id/
// commercial_objective_id/offer_id/tracking_requirement_id/causal/
// success/winner field exists here either.
export interface CommercialOutcomePublic {
  id: string;
  campaign_id: string;
  content_distribution_id: string | null;
  outcome_type: string;
  quantity: number | null;
  // NULL+NULL = unknown/undetermined; both non-null = a known value,
  // possibly "0" (a genuinely free/zero-value outcome).
  monetary_value: string | null;
  currency: string | null;
  occurred_at: string;
  created_at: string;
  external_reference: string | null;
  // Derived server-side (no other row's supersedes_outcome_id points at
  // this row) — never a second persisted status.
  is_current: boolean;
  supersedes_outcome_id: string | null;
  corrected_by_commercial_outcome_id: string | null;
  correction_reason: string | null;
}

// The writable fields for a new CommercialOutcome. client_request_id is
// required — idempotency is never optional for this route (MVP-36A-R1
// §3/§4).
export interface CreateCommercialOutcomeRequest {
  outcome_type: string;
  quantity?: number | null;
  monetary_value?: string | null;
  currency?: string | null;
  occurred_at: string;
  content_distribution_id?: string | null;
  external_reference?: string | null;
  client_request_id: string;
}

// FULL-STATE correction (MVP-36A-R1 §9) — the complete corrected claim,
// never a patch. content_distribution_id is deliberately ABSENT here:
// Distribution provenance is structurally immutable through a correction
// chain, always inherited unconditionally from the target row.
export interface CorrectCommercialOutcomeRequest {
  outcome_type: string;
  quantity?: number | null;
  monetary_value?: string | null;
  currency?: string | null;
  occurred_at: string;
  external_reference?: string | null;
  client_request_id: string;
  correction_reason: string;
}
