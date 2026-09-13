import { beforeEach, describe, expect, it, vi } from "vitest";

// Tests the API helper's own contract directly against a mocked
// request() — proving method/path/body without going through any
// component, per MVP-13B-B §26 (explicitly not repeating the MVP-12C-B
// P3 gap of only mocking the helper at the component boundary).
vi.mock("@/lib/api/client", () => ({
  request: vi.fn(),
}));

import { request } from "@/lib/api/client";
import { getWorkspaceSettings, updateWorkspaceSettings } from "@/lib/api/settings";
import type { WorkspaceSettingsResponse } from "@/types/settings";

const mockRequest = vi.mocked(request);

beforeEach(() => {
  vi.clearAllMocks();
});

function emptySettings(): WorkspaceSettingsResponse {
  return {
    workspace: { name: "x" },
    ai_preferences: { tone: null, depth: null, creativity: null },
    notifications: {
      campaign_ready: true,
      content_review: true,
      metrics_available: true,
      analysis_complete: true,
      weekly_summary: true,
    },
  };
}

describe("settings API client", () => {
  it("getWorkspaceSettings issues an exact GET to the encoded workspace path", async () => {
    mockRequest.mockResolvedValue(emptySettings());

    await getWorkspaceSettings("ws 1/2");

    expect(mockRequest).toHaveBeenCalledTimes(1);
    expect(mockRequest).toHaveBeenCalledWith("/workspaces/ws%201%2F2/settings", { method: "GET" });
  });

  it("updateWorkspaceSettings issues an exact PATCH with the exact workspace-name payload", async () => {
    mockRequest.mockResolvedValue(emptySettings());

    await updateWorkspaceSettings("ws-1", { workspace: { name: "New name" } });

    expect(mockRequest).toHaveBeenCalledWith("/workspaces/ws-1/settings", {
      method: "PATCH",
      body: { workspace: { name: "New name" } },
    });
  });

  it("sends only the ai_preferences key for a partial AI payload — no workspace/notifications smuggled in", async () => {
    mockRequest.mockResolvedValue(emptySettings());

    await updateWorkspaceSettings("ws-1", { ai_preferences: { tone: "profesional" } });

    const [, options] = mockRequest.mock.calls[0];
    expect(options).toEqual({ method: "PATCH", body: { ai_preferences: { tone: "profesional" } } });
    expect(Object.keys((options as { body: Record<string, unknown> }).body)).toEqual(["ai_preferences"]);
  });

  it("sends a single-key partial payload for one notification toggle", async () => {
    mockRequest.mockResolvedValue(emptySettings());

    await updateWorkspaceSettings("ws-1", { notifications: { weekly_summary: false } });

    expect(mockRequest).toHaveBeenCalledWith("/workspaces/ws-1/settings", {
      method: "PATCH",
      body: { notifications: { weekly_summary: false } },
    });
  });
});
