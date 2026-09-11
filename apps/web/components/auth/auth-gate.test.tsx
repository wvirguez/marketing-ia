import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { AuthGate } from "@/components/auth/auth-gate";
import type { AuthContextValue } from "@/lib/auth/auth-context";
import type { SessionContext } from "@/types/auth";

vi.mock("next/navigation", () => ({
  useRouter: vi.fn(),
}));

vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: vi.fn(),
}));

import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth/auth-context";

const mockUseRouter = vi.mocked(useRouter);
const mockUseAuth = vi.mocked(useAuth);

function makeSession(): SessionContext {
  return {
    user: { id: "user-1", email: "user@example.com", display_name: "Usuaria", status: "ACTIVE", preferences: { locale: null, timezone: null } },
    workspace: { id: "workspace-1", name: "Workspace", slug: "workspace" },
    membership: { role: "OWNER" },
  };
}

function authValue(overrides: Partial<AuthContextValue>): AuthContextValue {
  return {
    status: "loading",
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
    ...overrides,
  } as AuthContextValue;
}

const PROTECTED_CONTENT = "Contenido protegido";

beforeEach(() => {
  vi.clearAllMocks();
  mockUseRouter.mockReturnValue({ replace: vi.fn() } as unknown as ReturnType<typeof useRouter>);
});

describe("AuthGate", () => {
  it("renders the loading presentation and does not redirect while auth is loading", () => {
    mockUseAuth.mockReturnValue(authValue({ status: "loading" }));
    const replace = vi.fn();
    mockUseRouter.mockReturnValue({ replace } as unknown as ReturnType<typeof useRouter>);

    render(
      <AuthGate requireStatus="authenticated" redirectTo="/login">
        <p>{PROTECTED_CONTENT}</p>
      </AuthGate>,
    );

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByText(PROTECTED_CONTENT)).not.toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("renders children and does not redirect when the required status matches the actual status", () => {
    mockUseAuth.mockReturnValue(authValue({ status: "authenticated", session: makeSession() }));
    const replace = vi.fn();
    mockUseRouter.mockReturnValue({ replace } as unknown as ReturnType<typeof useRouter>);

    render(
      <AuthGate requireStatus="authenticated" redirectTo="/login">
        <p>{PROTECTED_CONTENT}</p>
      </AuthGate>,
    );

    expect(screen.getByText(PROTECTED_CONTENT)).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("redirects to the configured target and withholds protected children when unauthenticated", () => {
    mockUseAuth.mockReturnValue(authValue({ status: "unauthenticated" }));
    const replace = vi.fn();
    mockUseRouter.mockReturnValue({ replace } as unknown as ReturnType<typeof useRouter>);

    render(
      <AuthGate requireStatus="authenticated" redirectTo="/login">
        <p>{PROTECTED_CONTENT}</p>
      </AuthGate>,
    );

    expect(replace).toHaveBeenCalledWith("/login");
    expect(screen.queryByText(PROTECTED_CONTENT)).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("redirects an already-authenticated visitor away from an unauthenticated-only route", () => {
    mockUseAuth.mockReturnValue(authValue({ status: "authenticated", session: makeSession() }));
    const replace = vi.fn();
    mockUseRouter.mockReturnValue({ replace } as unknown as ReturnType<typeof useRouter>);

    render(
      <AuthGate requireStatus="unauthenticated" redirectTo="/">
        <p>Formulario de inicio de sesión</p>
      </AuthGate>,
    );

    expect(replace).toHaveBeenCalledWith("/");
    expect(screen.queryByText("Formulario de inicio de sesión")).not.toBeInTheDocument();
  });
});
