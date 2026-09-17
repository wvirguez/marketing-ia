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
