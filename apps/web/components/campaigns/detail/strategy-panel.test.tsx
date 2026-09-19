import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrategyPanel } from "@/components/campaigns/detail/strategy-panel";
import { ApiError } from "@/lib/api/client";
import type { ExperimentPublic, HypothesisPublic, StrategyOutputResponse, StrategyPublic } from "@/types/strategy";

vi.mock("@/lib/api/strategy", () => ({
  getStrategy: vi.fn(),
  createHypothesis: vi.fn(),
  createExperiment: vi.fn(),
  declareExperimentDefinition: vi.fn(),
  declareVariant: vi.fn(),
  listVariants: vi.fn(),
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

import { createExperiment, createHypothesis, declareExperimentDefinition, getStrategy } from "@/lib/api/strategy";
import { getStrategicApprovals } from "@/lib/api/strategic-approvals";
import { getStrategicDecisions } from "@/lib/api/strategic-decisions";
import { getStrategyHistory } from "@/lib/api/strategy-revisions";
import { useAuth } from "@/lib/auth/auth-context";

const mockGetStrategy = vi.mocked(getStrategy);
const mockCreateHypothesis = vi.mocked(createHypothesis);
const mockCreateExperiment = vi.mocked(createExperiment);
const mockDeclareDefinition = vi.mocked(declareExperimentDefinition);
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

function experiment(overrides: Partial<ExperimentPublic> = {}): ExperimentPublic {
  return {
    id: "EXP-1",
    hypothesis_id: "HYP-1",
    description: "A/B test two onboarding email sequences against a held-out control group.",
    status: "RECORDED",
    created_at: "2026-01-01T00:00:00Z",
    comparison_label: "NO_COMPARISON_DECLARED",
    definition: null,
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

describe("StrategyPanel — Experiment creation authority", () => {
  it("MEMBER sees the proposal control under a hypothesis", async () => {
    mockAuth("MEMBER");
    mockGetStrategy.mockResolvedValue(output({ hypotheses: [hypothesis()] }));
    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer experimento")).toBeInTheDocument());
  });

  it("an unauthenticated viewer never sees the proposal control", async () => {
    mockAuth(null);
    mockGetStrategy.mockResolvedValue(output({ hypotheses: [hypothesis()] }));
    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText(hypothesis().statement)).toBeInTheDocument());
    expect(screen.queryByText("Proponer experimento")).not.toBeInTheDocument();
  });
});

describe("StrategyPanel — Experiment creation submission", () => {
  it("OWNER can submit a new experiment and the list refreshes", async () => {
    mockAuth("OWNER");
    mockGetStrategy.mockResolvedValueOnce(output({ hypotheses: [hypothesis()] }));
    mockCreateExperiment.mockResolvedValue(experiment());
    mockGetStrategy.mockResolvedValueOnce(output({ hypotheses: [hypothesis()], experiments: [experiment()] }));

    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer experimento")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer experimento"));

    await userEvent.type(
      screen.getByLabelText("Nuevo experimento"),
      "A/B test two onboarding email sequences against a held-out control group.",
    );
    await userEvent.click(screen.getByText("Confirmar experimento"));

    await waitFor(() =>
      expect(mockCreateExperiment).toHaveBeenCalledWith(
        "campaign-1",
        "HYP-1",
        "A/B test two onboarding email sequences against a held-out control group.",
      ),
    );
    await waitFor(() =>
      expect(
        screen.getByText("A/B test two onboarding email sequences against a held-out control group."),
      ).toBeInTheDocument(),
    );
  });

  it("the confirm button stays disabled for a blank description", async () => {
    mockAuth("OWNER");
    mockGetStrategy.mockResolvedValue(output({ hypotheses: [hypothesis()] }));
    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer experimento")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer experimento"));
    expect(screen.getByText("Confirmar experimento")).toBeDisabled();
  });

  it("surfaces a mutation error without losing the form", async () => {
    mockAuth("OWNER");
    mockGetStrategy.mockResolvedValue(output({ hypotheses: [hypothesis()] }));
    mockCreateExperiment.mockRejectedValue(new ApiError(409, "EXPERIMENT_STRATEGY_STALE", "Stale."));

    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer experimento")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer experimento"));
    await userEvent.type(screen.getByLabelText("Nuevo experimento"), "x");
    await userEvent.click(screen.getByText("Confirmar experimento"));

    await waitFor(() => expect(screen.getByText("No pudimos completar esta acción. Intenta de nuevo.")).toBeInTheDocument());
    expect(screen.getByLabelText("Nuevo experimento")).toBeInTheDocument();
  });

  it("cancel clears the form without submitting", async () => {
    mockAuth("OWNER");
    mockGetStrategy.mockResolvedValue(output({ hypotheses: [hypothesis()] }));
    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer experimento")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer experimento"));
    await userEvent.type(screen.getByLabelText("Nuevo experimento"), "discarded");
    await userEvent.click(screen.getByText("Cancelar"));

    expect(screen.queryByLabelText("Nuevo experimento")).not.toBeInTheDocument();
    expect(mockCreateExperiment).not.toHaveBeenCalled();
  });
});

describe("StrategyPanel — Experiment Definition (MVP-37)", () => {
  const observationalTip = {
    id: "EXD-1",
    experiment_id: "EXP-1",
    version: 1,
    comparison_question: "Does a question hook change completion?",
    comparison_type: "OBSERVATIONAL" as const,
    changed_factor: "Opening hook",
    controlled_factors: [],
    comparison_basis: "The current hook.",
    scope: "Reels, one month.",
    learning_intent: "Choose the next hook style.",
    non_conclusion_boundary: "Does not establish causality.",
    non_conclusion_codes: ["NO_ATTRIBUTION_ESTABLISHED", "CANNOT_ESTABLISH_CAUSALITY"],
    created_at: "2026-01-01T00:00:00Z",
    variant_count: 0,
    is_pinned: false,
  };

  it("shows the no-comparison state and a declare control under a definition-less experiment", async () => {
    mockAuth("MEMBER");
    mockGetStrategy.mockResolvedValue(output({ hypotheses: [hypothesis()], experiments: [experiment()] }));
    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Sin comparación declarada")).toBeInTheDocument());
    expect(screen.getByText("Declarar comparación")).toBeInTheDocument();
    // The Experiment's own status is untouched by the definition surface.
    expect(screen.getByText("RECORDED")).toBeInTheDocument();
  });

  it("shows the declared tip with its declaration-only label", async () => {
    mockAuth("OWNER");
    mockGetStrategy.mockResolvedValue(
      output({
        hypotheses: [hypothesis()],
        experiments: [
          experiment({ comparison_label: "DECLARED_OBSERVATIONAL_INTENT", definition: observationalTip }),
        ],
      }),
    );
    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Comparación observacional declarada")).toBeInTheDocument());
    expect(screen.getByText("Revisar comparación")).toBeInTheDocument();
    expect(screen.getByText("Una comparación observacional no puede establecer causalidad.")).toBeInTheDocument();
  });

  it("declaring a comparison refetches the strategy output and shows the new tip", async () => {
    mockAuth("OWNER");
    mockGetStrategy.mockResolvedValueOnce(output({ hypotheses: [hypothesis()], experiments: [experiment()] }));
    mockDeclareDefinition.mockResolvedValue(observationalTip);
    mockGetStrategy.mockResolvedValueOnce(
      output({
        hypotheses: [hypothesis()],
        experiments: [
          experiment({ comparison_label: "DECLARED_OBSERVATIONAL_INTENT", definition: observationalTip }),
        ],
      }),
    );

    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Declarar comparación")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Declarar comparación"));
    for (const [label, value] of [
      ["Pregunta de comparación", "Does a question hook change completion?"],
      ["Factor que cambia", "Opening hook"],
      ["Base de comparación", "The current hook."],
      ["Alcance", "Reels, one month."],
      ["Intención de aprendizaje", "Choose the next hook style."],
      ["Límite de conclusión (lo que esta comparación NO establece)", "Does not establish causality."],
    ] as const) {
      await userEvent.type(screen.getByLabelText(label), value);
    }
    await userEvent.click(screen.getByText("Confirmar comparación"));

    await waitFor(() => expect(mockDeclareDefinition).toHaveBeenCalledTimes(1));
    expect(mockDeclareDefinition.mock.calls[0][0]).toBe("campaign-1");
    expect(mockDeclareDefinition.mock.calls[0][1]).toBe("EXP-1");
    expect(mockDeclareDefinition.mock.calls[0][2].base_version).toBe(0);
    await waitFor(() => expect(screen.getByText("Comparación observacional declarada")).toBeInTheDocument());
    expect(mockGetStrategy).toHaveBeenCalledTimes(2);
  });

  it("an unauthenticated viewer never sees the declare control", async () => {
    mockAuth(null);
    mockGetStrategy.mockResolvedValue(output({ hypotheses: [hypothesis()], experiments: [experiment()] }));
    render(<StrategyPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Sin comparación declarada")).toBeInTheDocument());
    expect(screen.queryByText("Declarar comparación")).not.toBeInTheDocument();
  });
});
