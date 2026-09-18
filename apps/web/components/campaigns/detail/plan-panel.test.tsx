import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PlanPanel } from "@/components/campaigns/detail/plan-panel";
import { ApiError } from "@/lib/api/client";
import type { ContentBriefPublic, ContentPlanPublic, PlanItemPublic, PlanOutputResponse } from "@/types/planning";
import type { ExperimentPublic, StrategyOutputResponse } from "@/types/strategy";

vi.mock("@/lib/api/planning", () => ({
  getPlan: vi.fn(),
  createPlan: vi.fn(),
  createBrief: vi.fn(),
}));
vi.mock("@/lib/api/strategy", () => ({
  getStrategy: vi.fn(),
}));
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: vi.fn(),
}));

import { createBrief, createPlan, getPlan } from "@/lib/api/planning";
import { getStrategy } from "@/lib/api/strategy";
import { useAuth } from "@/lib/auth/auth-context";

const mockGetPlan = vi.mocked(getPlan);
const mockCreatePlan = vi.mocked(createPlan);
const mockCreateBrief = vi.mocked(createBrief);
const mockGetStrategy = vi.mocked(getStrategy);
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

function experiment(overrides: Partial<ExperimentPublic> = {}): ExperimentPublic {
  return {
    id: "EXP-1",
    hypothesis_id: "HYP-1",
    description: "A/B test two onboarding email sequences.",
    status: "RECORDED",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function strategyOutput(overrides: Partial<StrategyOutputResponse> = {}): StrategyOutputResponse {
  return {
    strategy: { id: "STR-1", campaign_id: "campaign-1", version: 1, origin: "BOOTSTRAP", summary: "x", created_at: "2026-01-01T00:00:00Z" },
    positioning: { id: "POS-1", statement: "x", created_at: "2026-01-01T00:00:00Z" },
    hypotheses: [],
    experiments: [],
    ...overrides,
  };
}

function plan(overrides: Partial<ContentPlanPublic> = {}): ContentPlanPublic {
  return {
    id: "PLN-1",
    campaign_id: "campaign-1",
    experiment_id: null,
    version: 1,
    summary: "Existing plan summary.",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function planItem(overrides: Partial<PlanItemPublic> = {}): PlanItemPublic {
  return {
    id: "ITM-1",
    format: "Reel",
    objective: "Introduce the offer.",
    sequence: 1,
    scheduled_date: null,
    created_at: "2026-01-01T00:00:00Z",
    brief: null,
    ...overrides,
  };
}

function contentBrief(overrides: Partial<ContentBriefPublic> = {}): ContentBriefPublic {
  return {
    id: "CBRF-1",
    plan_item_id: "ITM-1",
    content_plan_id: "PLN-1",
    brief: "Produce a beginner-friendly reel.",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function output(overrides: Partial<PlanOutputResponse> = {}): PlanOutputResponse {
  return { plan: plan(), items: [planItem()], ...overrides };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetStrategy.mockResolvedValue(strategyOutput());
});

describe("PlanPanel — existing read behavior remains intact", () => {
  it("renders the current plan summary and items", async () => {
    mockAuth(null);
    mockGetPlan.mockResolvedValue(output());
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Existing plan summary.")).toBeInTheDocument());
    expect(screen.getByText("Introduce the offer.")).toBeInTheDocument();
  });

  it("shows the before-start copy when no plan exists yet", async () => {
    mockAuth(null);
    mockGetPlan.mockResolvedValue({ plan: null, items: [] });
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() =>
      expect(
        screen.getByText("Aún no se ha generado el borrador de planificación para esta campaña."),
      ).toBeInTheDocument(),
    );
  });
});

describe("PlanPanel — Plan creation authority", () => {
  it("MEMBER sees the proposal control", async () => {
    mockAuth("MEMBER");
    mockGetPlan.mockResolvedValue(output());
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
  });

  it("VIEWER/unauthenticated never sees the proposal control", async () => {
    mockAuth(null);
    mockGetPlan.mockResolvedValue(output());
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Existing plan summary.")).toBeInTheDocument());
    expect(screen.queryByText("Proponer plan")).not.toBeInTheDocument();
  });

  it("the proposal control is also available before any plan has been generated", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue({ plan: null, items: [] });
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
  });
});

describe("PlanPanel — Plan creation submission", () => {
  it("the confirm button stays disabled for a blank summary", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output());
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer plan"));
    expect(screen.getByText("Confirmar plan")).toBeDisabled();
  });

  it("the experiment selector defaults to no selection and shows current-Strategy experiments", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output());
    mockGetStrategy.mockResolvedValue(strategyOutput({ experiments: [experiment()] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer plan"));

    const select = (await screen.findByLabelText(
      "Experimento que operacionaliza este plan (opcional)",
    )) as HTMLSelectElement;
    expect(select.value).toBe("");
    await waitFor(() => expect(screen.getByText("EXP-1 — A/B test two onboarding email sequences.")).toBeInTheDocument());
  });

  it("generic submission sends no experiment_public_id", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValueOnce(output({ plan: null, items: [] }));
    mockCreatePlan.mockResolvedValue(plan());
    mockGetPlan.mockResolvedValueOnce(output());

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer plan"));
    await userEvent.type(screen.getByLabelText("Nuevo plan"), "A generic content plan.");
    await userEvent.click(screen.getByText("Confirmar plan"));

    await waitFor(() =>
      expect(mockCreatePlan).toHaveBeenCalledWith("campaign-1", "A generic content plan.", null),
    );
    await waitFor(() => expect(screen.getByText("Existing plan summary.")).toBeInTheDocument());
  });

  it("experiment-derived submission sends the selected public id", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValueOnce(output({ plan: null, items: [] }));
    mockGetStrategy.mockResolvedValue(strategyOutput({ experiments: [experiment()] }));
    mockCreatePlan.mockResolvedValue(plan({ experiment_id: "EXP-1" }));
    mockGetPlan.mockResolvedValueOnce(output({ plan: plan({ experiment_id: "EXP-1" }) }));

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer plan"));
    await userEvent.type(screen.getByLabelText("Nuevo plan"), "Operationalizes the experiment.");
    await waitFor(() => expect(screen.getByText("EXP-1 — A/B test two onboarding email sequences.")).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText("Experimento que operacionaliza este plan (opcional)"), "EXP-1");
    await userEvent.click(screen.getByText("Confirmar plan"));

    await waitFor(() =>
      expect(mockCreatePlan).toHaveBeenCalledWith("campaign-1", "Operationalizes the experiment.", "EXP-1"),
    );
  });

  it("surfaces a mutation error without losing the form", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output());
    mockCreatePlan.mockRejectedValue(new ApiError(409, "VERSION_CONFLICT", "Conflict."));

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer plan"));
    await userEvent.type(screen.getByLabelText("Nuevo plan"), "x");
    await userEvent.click(screen.getByText("Confirmar plan"));

    await waitFor(() => expect(screen.getByText("No pudimos completar esta acción. Intenta de nuevo.")).toBeInTheDocument());
    expect(screen.getByLabelText("Nuevo plan")).toBeInTheDocument();
  });

  it("cancel clears the form without submitting", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output());
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer plan"));
    await userEvent.type(screen.getByLabelText("Nuevo plan"), "discarded");
    await userEvent.click(screen.getByText("Cancelar"));

    expect(screen.queryByLabelText("Nuevo plan")).not.toBeInTheDocument();
    expect(mockCreatePlan).not.toHaveBeenCalled();
  });
});

describe("PlanPanel — Content Brief creation (MVP-34A/-34B)", () => {
  it("MEMBER+ sees the create affordance for an unbriefed item", async () => {
    mockAuth("MEMBER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: null })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Redactar brief")).toBeInTheDocument());
  });

  it("unauthenticated never sees the create affordance", async () => {
    mockAuth(null);
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: null })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Introduce the offer.")).toBeInTheDocument());
    expect(screen.queryByText("Redactar brief")).not.toBeInTheDocument();
  });

  it("submits the exact payload for the targeted Plan Item", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValueOnce(output({ items: [planItem({ id: "ITM-7", brief: null })] }));
    mockCreateBrief.mockResolvedValue(contentBrief({ plan_item_id: "ITM-7" }));
    mockGetPlan.mockResolvedValueOnce(
      output({ items: [planItem({ id: "ITM-7", brief: contentBrief({ plan_item_id: "ITM-7" }) })] }),
    );

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Redactar brief")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Redactar brief"));
    await userEvent.type(screen.getByLabelText("Brief para este elemento"), "Produce a beginner reel.");
    await userEvent.click(screen.getByText("Confirmar brief"));

    await waitFor(() =>
      expect(mockCreateBrief).toHaveBeenCalledWith("campaign-1", "ITM-7", "Produce a beginner reel."),
    );
  });

  it("re-renders the persisted Brief read-only after a successful create", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValueOnce(output({ items: [planItem({ brief: null })] }));
    mockCreateBrief.mockResolvedValue(contentBrief());
    mockGetPlan.mockResolvedValueOnce(output({ items: [planItem({ brief: contentBrief() })] }));

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Redactar brief")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Redactar brief"));
    await userEvent.type(screen.getByLabelText("Brief para este elemento"), "x");
    await userEvent.click(screen.getByText("Confirmar brief"));

    await waitFor(() =>
      expect(screen.getByText("Brief: Produce a beginner-friendly reel.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Redactar brief")).not.toBeInTheDocument();
  });

  it("displays an existing Brief read-only, with no edit/delete/re-brief affordance", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: contentBrief() })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() =>
      expect(screen.getByText("Brief: Produce a beginner-friendly reel.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Redactar brief")).not.toBeInTheDocument();
    expect(screen.queryByText("Editar")).not.toBeInTheDocument();
    expect(screen.queryByText("Eliminar")).not.toBeInTheDocument();
  });

  it("surfaces a mutation error (e.g. 409 duplicate) without losing the form", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: null })] }));
    mockCreateBrief.mockRejectedValue(new ApiError(409, "PLAN_ITEM_ALREADY_BRIEFED", "Conflict."));

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Redactar brief")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Redactar brief"));
    await userEvent.type(screen.getByLabelText("Brief para este elemento"), "x");
    await userEvent.click(screen.getByText("Confirmar brief"));

    await waitFor(() => expect(screen.getByText("No pudimos completar esta acción. Intenta de nuevo.")).toBeInTheDocument());
    expect(screen.getByLabelText("Brief para este elemento")).toBeInTheDocument();
  });

  it("cancel clears the brief form without submitting", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: null })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Redactar brief")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Redactar brief"));
    await userEvent.type(screen.getByLabelText("Brief para este elemento"), "discarded");
    await userEvent.click(screen.getByText("Cancelar"));

    expect(screen.queryByLabelText("Brief para este elemento")).not.toBeInTheDocument();
    expect(mockCreateBrief).not.toHaveBeenCalled();
  });

  it("does not render any Variant or experimental-arm affordance", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: null })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Redactar brief")).toBeInTheDocument());
    expect(screen.queryByText(/variant/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/control/i)).not.toBeInTheDocument();
  });
});
