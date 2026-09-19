// Typed service functions for the Content contract. Mirrors
// apps/api/app/content/router.py exactly: GET (read), and the six
// lifecycle/approval POST routes added by MVP-17B and the two human-recorded
// Distribution transitions added by MVP-18B.

import { request } from "@/lib/api/client";
import type {
  ContentPieceDetailResponse,
  ContentPieceListResponse,
  ContentApprovalDecision,
  CreateContentPieceRequest,
  CreateContentVersionRequest,
  DistributionEvidenceListResponse,
  DistributionEvidencePublic,
  DistributionEvidenceSummaryPublic,
  RecordDistributionEvidenceCorrectionRequest,
  RecordDistributionEvidenceRequest,
} from "@/types/content";

function contentPath(campaignPublicId: string, contentPublicId: string): string {
  return `/campaigns/${encodeURIComponent(campaignPublicId)}/content/${encodeURIComponent(contentPublicId)}`;
}

export async function listContent(campaignPublicId: string): Promise<ContentPieceListResponse> {
  return request<ContentPieceListResponse>(`/campaigns/${encodeURIComponent(campaignPublicId)}/content`, {
    method: "GET",
  });
}

export async function getContentDetail(
  campaignPublicId: string,
  contentPublicId: string,
): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(contentPath(campaignPublicId, contentPublicId), { method: "GET" });
}

// MVP-35A/-35B: governed, ContentBrief 1 -> 0..N ContentPiece creation —
// mirrors apps/api/app/content/router.py::create_content_piece exactly.
export async function createContentPiece(
  campaignPublicId: string,
  contentBriefPublicId: string,
  body: CreateContentPieceRequest,
): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(
    `/campaigns/${encodeURIComponent(campaignPublicId)}/content/briefs/${encodeURIComponent(contentBriefPublicId)}/pieces`,
    { method: "POST", body },
  );
}

export async function markContentInProduction(
  campaignPublicId: string,
  contentPublicId: string,
): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(`${contentPath(campaignPublicId, contentPublicId)}/mark-in-production`, {
    method: "POST",
  });
}

export async function markContentProduced(
  campaignPublicId: string,
  contentPublicId: string,
): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(`${contentPath(campaignPublicId, contentPublicId)}/mark-produced`, {
    method: "POST",
  });
}

export async function markContentReadyForReview(
  campaignPublicId: string,
  contentPublicId: string,
): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(`${contentPath(campaignPublicId, contentPublicId)}/mark-ready-for-review`, {
    method: "POST",
  });
}

// MVP-20: the only route authorized to move a piece out of
// REVISION_REQUESTED — legal only there, and only via this call, never
// via markContentInProduction.
export async function createContentRevisionVersion(
  campaignPublicId: string,
  contentPublicId: string,
  body: CreateContentVersionRequest,
): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(`${contentPath(campaignPublicId, contentPublicId)}/versions`, {
    method: "POST",
    body,
  });
}

export async function requestContentApproval(
  campaignPublicId: string,
  contentPublicId: string,
): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(`${contentPath(campaignPublicId, contentPublicId)}/request-approval`, {
    method: "POST",
  });
}

export async function markContentApprovalUnderReview(
  campaignPublicId: string,
  contentPublicId: string,
  approvalPublicId: string,
): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(
    `${contentPath(campaignPublicId, contentPublicId)}/approvals/${encodeURIComponent(approvalPublicId)}/mark-under-review`,
    { method: "POST" },
  );
}

export async function recordContentApprovalDecision(
  campaignPublicId: string,
  contentPublicId: string,
  approvalPublicId: string,
  decision: ContentApprovalDecision,
): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(
    `${contentPath(campaignPublicId, contentPublicId)}/approvals/${encodeURIComponent(approvalPublicId)}/decision`,
    { method: "POST", body: { decision } },
  );
}

export async function markReadyForDistribution(campaignPublicId: string, contentPublicId: string): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(
    `${contentPath(campaignPublicId, contentPublicId)}/distribution/mark-ready-for-distribution`,
    { method: "POST" },
  );
}

export async function recordDistributed(
  campaignPublicId: string, contentPublicId: string, externalReference: string | null,
): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(
    `${contentPath(campaignPublicId, contentPublicId)}/distribution/record-distributed`,
    { method: "POST", body: { external_reference: externalReference } },
  );
}

// MVP-24: ContentDistribution <-> TrackingRequirement association.
// IDENTITY-ONLY (MVP-24A-R1) — never returns/implies TrackingRequirement
// state, only pair identity via the canonical Distribution representation.

export async function associateTrackingRequirement(
  campaignPublicId: string, contentPublicId: string, trackingRequirementId: string,
): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(
    `${contentPath(campaignPublicId, contentPublicId)}/distribution/tracking-requirements`,
    { method: "POST", body: { tracking_requirement_id: trackingRequirementId } },
  );
}

export async function dissociateTrackingRequirement(
  campaignPublicId: string, contentPublicId: string, trackingRequirementId: string,
): Promise<ContentPieceDetailResponse> {
  return request<ContentPieceDetailResponse>(
    `${contentPath(campaignPublicId, contentPublicId)}/distribution/tracking-requirements/${encodeURIComponent(trackingRequirementId)}/remove`,
    { method: "POST" },
  );
}

// MVP-19B: Distribution-linked Measurement Evidence. Mirrors
// apps/api/app/measurement/router.py's evidence routes exactly — no
// channel, source, or Distribution id is ever sent by the client; the
// server resolves and freezes all of that from the Distribution itself.

export async function listDistributionEvidence(
  campaignPublicId: string,
  contentPublicId: string,
  params?: { limit?: number; offset?: number },
): Promise<DistributionEvidenceListResponse> {
  const query = new URLSearchParams();
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  if (params?.offset !== undefined) query.set("offset", String(params.offset));
  const suffix = query.toString();
  return request<DistributionEvidenceListResponse>(
    `${contentPath(campaignPublicId, contentPublicId)}/distribution/evidence${suffix ? `?${suffix}` : ""}`,
    { method: "GET" },
  );
}

export async function recordDistributionEvidence(
  campaignPublicId: string,
  contentPublicId: string,
  body: RecordDistributionEvidenceRequest,
): Promise<DistributionEvidencePublic> {
  return request<DistributionEvidencePublic>(`${contentPath(campaignPublicId, contentPublicId)}/distribution/evidence`, {
    method: "POST",
    body,
  });
}

export async function getDistributionEvidenceSummary(
  campaignPublicId: string,
  contentPublicId: string,
): Promise<DistributionEvidenceSummaryPublic> {
  return request<DistributionEvidenceSummaryPublic>(
    `${contentPath(campaignPublicId, contentPublicId)}/distribution/evidence/summary`,
    { method: "GET" },
  );
}

export async function recordDistributionEvidenceCorrection(
  campaignPublicId: string,
  contentPublicId: string,
  evidencePublicId: string,
  body: RecordDistributionEvidenceCorrectionRequest,
): Promise<DistributionEvidencePublic> {
  return request<DistributionEvidencePublic>(
    `${contentPath(campaignPublicId, contentPublicId)}/distribution/evidence/${encodeURIComponent(evidencePublicId)}/corrections`,
    { method: "POST", body },
  );
}
