// Typed service functions for the Workspace Settings contract. Mirrors
// apps/api/app/workspaces/router.py's GET/PATCH
// /workspaces/{workspace_public_id}/settings exactly (MVP-13B). Covers the
// Workspace-profile + AIPreference + NotificationPreference sections in one
// round trip — Profile (`/users/me`) is a separate contract, wrapped in
// lib/api/auth.ts.

import { request } from "@/lib/api/client";
import type { WorkspaceSettingsPatchRequest, WorkspaceSettingsResponse } from "@/types/settings";

export async function getWorkspaceSettings(workspacePublicId: string): Promise<WorkspaceSettingsResponse> {
  return request<WorkspaceSettingsResponse>(`/workspaces/${encodeURIComponent(workspacePublicId)}/settings`, {
    method: "GET",
  });
}

/** Callers must include only the top-level section(s) actually being
 * changed (`workspace`/`ai_preferences`/`notifications`) — an omitted
 * section is left untouched server-side; there is no "send everything"
 * requirement or behavior here. */
export async function updateWorkspaceSettings(
  workspacePublicId: string,
  payload: WorkspaceSettingsPatchRequest,
): Promise<WorkspaceSettingsResponse> {
  return request<WorkspaceSettingsResponse>(`/workspaces/${encodeURIComponent(workspacePublicId)}/settings`, {
    method: "PATCH",
    body: payload,
  });
}
