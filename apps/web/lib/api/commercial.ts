// Typed service functions for the Commercial contract. Mirrors
// apps/api/app/commercial/router.py exactly (MVP-27B):
// GET/POST /commercial-objectives, POST /commercial-objectives/{id}/supersede,
// GET/POST /offers, POST /offers/{id}/supersede.

import { request } from "@/lib/api/client";
import type {
  CommercialObjectivePublic,
  CommercialOutcomePublic,
  CorrectCommercialOutcomeRequest,
  CreateCommercialOutcomeRequest,
  OfferPublic,
} from "@/types/commercial";

function campaignPath(campaignPublicId: string, suffix: string): string {
  return `/campaigns/${encodeURIComponent(campaignPublicId)}${suffix}`;
}

export async function getCommercialObjectives(campaignPublicId: string): Promise<CommercialObjectivePublic[]> {
  return request<CommercialObjectivePublic[]>(campaignPath(campaignPublicId, "/commercial-objectives"), {
    method: "GET",
  });
}

// Creates one additional, independent, current CommercialObjective — never
// replaces an existing one.
export async function createCommercialObjective(
  campaignPublicId: string,
  statement: string,
): Promise<CommercialObjectivePublic> {
  return request<CommercialObjectivePublic>(campaignPath(campaignPublicId, "/commercial-objectives"), {
    method: "POST",
    body: { statement },
  });
}

// Atomically supersedes one specific existing CommercialObjective with a
// fresh replacement — distinct from createCommercialObjective above.
export async function supersedeCommercialObjective(
  campaignPublicId: string,
  objectivePublicId: string,
  statement: string,
): Promise<CommercialObjectivePublic> {
  return request<CommercialObjectivePublic>(
    campaignPath(campaignPublicId, `/commercial-objectives/${encodeURIComponent(objectivePublicId)}/supersede`),
    { method: "POST", body: { statement } },
  );
}

export async function getOffers(campaignPublicId: string): Promise<OfferPublic[]> {
  return request<OfferPublic[]>(campaignPath(campaignPublicId, "/offers"), { method: "GET" });
}

// Creates one additional, independent, current Offer — never replaces an
// existing one. price/currency must both be provided or both omitted.
export async function createOffer(
  campaignPublicId: string,
  statement: string,
  price?: string | null,
  currency?: string | null,
): Promise<OfferPublic> {
  return request<OfferPublic>(campaignPath(campaignPublicId, "/offers"), {
    method: "POST",
    body: { statement, price: price ?? null, currency: currency ?? null },
  });
}

// Atomically supersedes one specific existing Offer with a fresh
// replacement — distinct from createOffer above.
export async function supersedeOffer(
  campaignPublicId: string,
  offerPublicId: string,
  statement: string,
  price?: string | null,
  currency?: string | null,
): Promise<OfferPublic> {
  return request<OfferPublic>(campaignPath(campaignPublicId, `/offers/${encodeURIComponent(offerPublicId)}/supersede`), {
    method: "POST",
    body: { statement, price: price ?? null, currency: currency ?? null },
  });
}

// MVP-36 (frozen by MVP-36A/-R1): governed CommercialOutcome. Create and
// correction both return 201 for a genuinely new row and 200 for an exact
// idempotent replay — `request()` treats both as success, the caller
// never needs to branch on which one came back.

export async function getCommercialOutcomes(campaignPublicId: string): Promise<CommercialOutcomePublic[]> {
  return request<CommercialOutcomePublic[]>(campaignPath(campaignPublicId, "/commercial-outcomes"), {
    method: "GET",
  });
}

export async function createCommercialOutcome(
  campaignPublicId: string,
  body: CreateCommercialOutcomeRequest,
): Promise<CommercialOutcomePublic> {
  return request<CommercialOutcomePublic>(campaignPath(campaignPublicId, "/commercial-outcomes"), {
    method: "POST",
    body,
  });
}

export async function correctCommercialOutcome(
  campaignPublicId: string,
  outcomePublicId: string,
  body: CorrectCommercialOutcomeRequest,
): Promise<CommercialOutcomePublic> {
  return request<CommercialOutcomePublic>(
    campaignPath(campaignPublicId, `/commercial-outcomes/${encodeURIComponent(outcomePublicId)}/corrections`),
    { method: "POST", body },
  );
}
