import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrategyPanel } from "@/components/campaigns/detail/strategy-panel";
import { ApiError } from "@/lib/api/client";
import type { HypothesisPublic, StrategyOutputResponse, StrategyPublic } from "@/types/strategy";

vi.mock("@/lib/api/strategy", () => ({
  getStrategy: vi.fn(),
  createHypothesis: vi.fn(),
}));
vi.mock("@/lib/api/strategic-approvals", () => ({
  getStrategicApprovals: vi.fn(),
}));
vi.mock("@/lib/api/strategic-decisions", () => ({
  getStrategicDecisions: vi.fn(),
}));
vi.mock("@/lib/api/strategy-revisions", () => ({
  getStrategyHistory: vi.fn(),
  reviseStrategy: vi.fn(),
}));
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: vi.fn(),
}));

import { createHypothesis, getStrategy } from "@/lib/api/strategy";
import { getStrategicApprovals } from "@/lib/api/strategic-approvals";
import { getStrategicDecisions } from "@/lib/api/strategic-decisions";
import { getStrategyHistory } from "@/lib/api/strategy-revisions";
import { useAuth } from "@/lib/auth/auth-context";

const mockGetStrategy = vi.mocked(getStrategy);
const mockCreateHypothesis = vi.mocked(createHypothesis);
const mockGetApprovals = vi.mocked(getStrategicApprovals);
const mockGetDecisions = vi.mocked(getStrategicDecisions);
const mockGetHistory = vi.mocked(getStrategyHistory);
const mockUseAuth = vi.mocked(useAuth);

function mockAuth(role: "OWNER" | "ADMIN" | "MEMBER" | null) {
  if (role === null) {
    mockUseAuth.mockReturnValue({
      status: "unauthenticated",
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      refresh: vi.fn(),
    } as never);
    return;
  }
  mockUseAuth.mockReturnValue({
    status: "authenticated",
    session: {
      user: { id: "USR-1", email: "user@impulso.test", display_name: "User", status: "ACTIVE", preferences: { locale: null, timezone: null } },
      workspace: { id: "WS-1", name: "Workspace", slug: "workspace" },
      membership: { role },
    },
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  } as never);
}

function strategy(overrides: Partial<StrategyPublic> = {}): StrategyPublic {
  return {
    id: "STR-1",
    campaign_id: "campaign-1",
    version: 1,
    origin: "BOOTSTRAP",
    summary: "Position as the structured method.",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function hypothesis(overrides: Partial<HypothesisPublic> = {}): HypothesisPublic {
  return {
    id: "HYP-1",
    statement: "First-time buyers respond better to a guarantee.",
    status: "OPEN",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function output(overrides: Partial<StrategyOutputResponse> = {}): StrategyOutputResponse {
  return {
    strategy: strategy(),
    positioning: { id: "POS-1", statement: "For overwhelmed first-time owners.", created_at: "2026-01-01T00:00:00Z" },
    hypotheses: [],
    experiments: [],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetApprovals.mockResolvedValue([]);
  mockGetDecisions.mockResolvedValue([]);
  mockGetHistory.mockResolvedValue({ items: [] });
});

describe("StrategyPanel — Hypothesis creation authority", () => {
  it("MEMBER sees the proposal control", async () => {
    mockAuth("MEMBER");
    mockGetStrategy.mockResolvedValue(output());
    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer hipótesis")).toBeInTheDocument());
  });

  it("an unauthenticated viewer never sees the proposal control", async () => {
    mockAuth(null);
    mockGetStrategy.mockResolvedValue(output());
    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Resumen de estrategia")).toBeInTheDocument());
    expect(screen.queryByText("Proponer hipótesis")).not.toBeInTheDocument();
  });
});

describe("StrategyPanel — Hypothesis creation submission", () => {
  it("OWNER can submit a new hypothesis and the list refreshes", async () => {
    mockAuth("OWNER");
    mockGetStrategy.mockResolvedValueOnce(output());
    mockCreateHypothesis.mockResolvedValue(hypothesis());
    mockGetStrategy.mockResolvedValueOnce(output({ hypotheses: [hypothesis()] }));

    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer hipótesis")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer hipótesis"));

    await userEvent.type(screen.getByLabelText("Nueva hipótesis"), "A guarantee increases signups.");
    await userEvent.click(screen.getByText("Confirmar hipótesis"));

    await waitFor(() =>
      expect(mockCreateHypothesis).toHaveBeenCalledWith("campaign-1", "STR-1", "A guarantee increases signups."),
    );
    await waitFor(() =>
      expect(screen.getByText("First-time buyers respond better to a guarantee.")).toBeInTheDocument(),
    );
  });

  it("the confirm button stays disabled for a blank statement", async () => {
    mockAuth("OWNER");
    mockGetStrategy.mockResolvedValue(output());
    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer hipótesis")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer hipótesis"));
    expect(screen.getByText("Confirmar hipótesis")).toBeDisabled();
  });

  it("surfaces a mutation error without losing the form", async () => {
    mockAuth("OWNER");
    mockGetStrategy.mockResolvedValue(output());
    mockCreateHypothesis.mockRejectedValue(new ApiError(409, "HYPOTHESIS_STRATEGY_STALE", "Stale."));

    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer hipótesis")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer hipótesis"));
    await userEvent.type(screen.getByLabelText("Nueva hipótesis"), "x");
    await userEvent.click(screen.getByText("Confirmar hipótesis"));

    await waitFor(() => expect(screen.getByText("No pudimos completar esta acción. Intenta de nuevo.")).toBeInTheDocument());
    expect(screen.getByLabelText("Nueva hipótesis")).toBeInTheDocument();
  });

  it("cancel clears the form without submitting", async () => {
    mockAuth("OWNER");
    mockGetStrategy.mockResolvedValue(output());
    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer hipótesis")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer hipótesis"));
    await userEvent.type(screen.getByLabelText("Nueva hipótesis"), "discarded");
    await userEvent.click(screen.getByText("Cancelar"));

    expect(screen.queryByLabelText("Nueva hipótesis")).not.toBeInTheDocument();
    expect(mockCreateHypothesis).not.toHaveBeenCalled();
  });
});
