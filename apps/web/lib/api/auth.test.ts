import { beforeEach, describe, expect, it, vi } from "vitest";

// Direct API-helper contract test for updateCurrentUser — mocks
// request() itself, not the helper, per MVP-13B-B §26.
vi.mock("@/lib/api/client", () => ({
  request: vi.fn(),
  fetchCsrfToken: vi.fn(),
}));

import { request } from "@/lib/api/client";
import { updateCurrentUser } from "@/lib/api/auth";
import type { UserPublic } from "@/types/auth";

const mockRequest = vi.mocked(request);

beforeEach(() => {
  vi.clearAllMocks();
});

function makeUser(): UserPublic {
  return {
    id: "USR-1",
    email: "a@b.com",
    display_name: "New Name",
    status: "ACTIVE",
    preferences: { locale: "es", timezone: null },
  };
}

describe("auth API client — updateCurrentUser", () => {
  it("issues an exact PATCH to /users/me with only the submitted fields", async () => {
    mockRequest.mockResolvedValue(makeUser());

    await updateCurrentUser({ display_name: "New Name" });

    expect(mockRequest).toHaveBeenCalledTimes(1);
    expect(mockRequest).toHaveBeenCalledWith("/users/me", {
      method: "PATCH",
      body: { display_name: "New Name" },
    });
  });

  it("never includes an email field in the PATCH body", async () => {
    mockRequest.mockResolvedValue(makeUser());

    await updateCurrentUser({ preferences: { locale: "en" } });

    const [, options] = mockRequest.mock.calls[0];
    expect(options).toEqual({ method: "PATCH", body: { preferences: { locale: "en" } } });
    expect(JSON.stringify(options)).not.toContain("email");
  });
});
