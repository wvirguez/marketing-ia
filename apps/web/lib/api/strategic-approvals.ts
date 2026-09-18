// Typed service functions for the StrategicApproval contract. Mirrors
// apps/api/app/orchestration/strategic_approval_router.py exactly (MVP-29B):
// GET/POST /strategic-decisions/{id}/approval, GET /strategic-approvals.

import { request } from "@/lib/api/client";
import type { StrategicApprovalOutcome, StrategicApprovalPublic } from "@/types/strategic-approvals";

function campaignPath(campaignPublicId: string, suffix: string): string {
  return `/campaigns/${encodeURIComponent(campaignPublicId)}${suffix}`;
}

function decisionApprovalPath(campaignPublicId: string, decisionPublicId: string): string {
  return campaignPath(campaignPublicId, `/strategic-decisions/${encodeURIComponent(decisionPublicId)}/approval`);
}

// Returns `null` when the Decision exists but has no Approval yet — a
// legitimate, expected state, never conflated with the Decision itself
// not existing/being accessible (which throws `ApiError` instead).
export async function getStrategicApprovalForDecision(
  campaignPublicId: string,
  decisionPublicId: string,
): Promise<StrategicApprovalPublic | null> {
  return request<StrategicApprovalPublic | null>(decisionApprovalPath(campaignPublicId, decisionPublicId), {
    method: "GET",
  });
}

// Batched, campaign-wide read — used to display every Decision's own
// Approval status (or its absence) in one request rather than one
// request per rendered Decision (mirrors how the Decision list itself is
// fetched in bulk and then filtered client-side).
export async function getStrategicApprovals(campaignPublicId: string): Promise<StrategicApprovalPublic[]> {
  const response = await request<{ items: StrategicApprovalPublic[] }>(
    campaignPath(campaignPublicId, "/strategic-approvals"),
    { method: "GET" },
  );
  return response.items;
}

// One-shot terminal ruling — there is no "supersede"/"reopen" counterpart
// for StrategicApproval (MVP-29A §G/§J/§K).
export async function recordStrategicApproval(
  campaignPublicId: string,
  decisionPublicId: string,
  outcome: StrategicApprovalOutcome,
): Promise<StrategicApprovalPublic> {
  return request<StrategicApprovalPublic>(decisionApprovalPath(campaignPublicId, decisionPublicId), {
    method: "POST",
    body: { outcome },
  });
}
