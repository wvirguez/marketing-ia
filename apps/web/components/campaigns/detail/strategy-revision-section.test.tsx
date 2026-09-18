import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrategyRevisionSection } from "@/components/campaigns/detail/strategy-revision-section";
import { ApiError } from "@/lib/api/client";
import type { StrategicApprovalPublic } from "@/types/strategic-approvals";
import type { StrategicDecisionPublic } from "@/types/strategic-decisions";
import type { StrategyHistoryResponse, StrategyRevisionResult } from "@/types/strategy-revisions";

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

import { getStrategicApprovals } from "@/lib/api/strategic-approvals";
import { getStrategicDecisions } from "@/lib/api/strategic-decisions";
import { getStrategyHistory, reviseStrategy } from "@/lib/api/strategy-revisions";

const mockGetApprovals = vi.mocked(getStrategicApprovals);
const mockGetDecisions = vi.mocked(getStrategicDecisions);
const mockGetHistory = vi.mocked(getStrategyHistory);
const mockRevise = vi.mocked(reviseStrategy);

function decision(overrides: Partial<StrategicDecisionPublic> = {}): StrategicDecisionPublic {
  return {
    id: "DEC-1",
    campaign_id: "campaign-1",
    strategic_recommendation_candidate_id: "SRC-1",
    decision_type: "ADOPT",
    statement: "Adopt the shorter-hooks direction.",
    created_at: "2026-01-01T00:00:00Z",
    current: true,
    superseded_at: null,
    superseded_by_strategic_decision_id: null,
    ...overrides,
  };
}

function approval(overrides: Partial<StrategicApprovalPublic> = {}): StrategicApprovalPublic {
  return {
    id: "SAP-1",
    campaign_id: "campaign-1",
    strategic_decision_id: "DEC-1",
    outcome: "APPROVED",
    created_at: "2026-01-03T00:00:00Z",
    ...overrides,
  };
}

function historyResponse(items: StrategyHistoryResponse["items"] = []): StrategyHistoryResponse {
  return { items };
}

function strategyItem(id: string, overrides: Partial<StrategyHistoryResponse["items"][number]["strategy"]> = {}) {
  return {
    id,
    campaign_id: "campaign-1",
    version: 1,
    origin: "BOOTSTRAP" as const,
    summary: "Initial strategy.",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("StrategyRevisionSection — eligibility derivation", () => {
  it("shows no eligible-approval message when none exists", async () => {
    mockGetApprovals.mockResolvedValue([]);
    mockGetDecisions.mockResolvedValue([]);
    mockGetHistory.mockResolvedValue(historyResponse([{ strategy: strategyItem("STR-1"), revision: null }]));
    render(<StrategyRevisionSection campaignId="campaign-1" currentStrategyId="STR-1" role="OWNER" onRevised={vi.fn()} />);
    await waitFor(() =>
      expect(
        screen.getByText(/No hay ninguna Aprobación estratégica vigente y disponible/),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("Registrar revisión de estrategia")).not.toBeInTheDocument();
  });

  it("excludes a REJECTED approval from eligibility", async () => {
    mockGetApprovals.mockResolvedValue([approval({ outcome: "REJECTED" })]);
    mockGetDecisions.mockResolvedValue([decision()]);
    mockGetHistory.mockResolvedValue(historyResponse([{ strategy: strategyItem("STR-1"), revision: null }]));
    render(<StrategyRevisionSection campaignId="campaign-1" currentStrategyId="STR-1" role="OWNER" onRevised={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByText(/No hay ninguna Aprobación estratégica vigente y disponible/)).toBeInTheDocument(),
    );
  });

  it("excludes an approval whose decision is no longer current", async () => {
    mockGetApprovals.mockResolvedValue([approval()]);
    mockGetDecisions.mockResolvedValue([decision({ current: false })]);
    mockGetHistory.mockResolvedValue(historyResponse([{ strategy: strategyItem("STR-1"), revision: null }]));
    render(<StrategyRevisionSection campaignId="campaign-1" currentStrategyId="STR-1" role="OWNER" onRevised={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByText(/No hay ninguna Aprobación estratégica vigente y disponible/)).toBeInTheDocument(),
    );
  });

  it("excludes an approval already consumed by a prior revision", async () => {
    mockGetApprovals.mockResolvedValue([approval()]);
    mockGetDecisions.mockResolvedValue([decision()]);
    mockGetHistory.mockResolvedValue(
      historyResponse([
        { strategy: strategyItem("STR-1"), revision: null },
        {
          strategy: strategyItem("STR-2", { version: 2, origin: "REVISION" }),
          revision: {
            id: "SRV-1", campaign_id: "campaign-1", strategic_approval_id: "SAP-1",
            base_strategy_id: "STR-1", result_strategy_id: "STR-2", created_at: "2026-01-04T00:00:00Z",
          },
        },
      ]),
    );
    render(<StrategyRevisionSection campaignId="campaign-1" currentStrategyId="STR-2" role="OWNER" onRevised={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByText(/No hay ninguna Aprobación estratégica vigente y disponible/)).toBeInTheDocument(),
    );
  });

  it("includes an ADOPT + current + APPROVED + unconsumed approval", async () => {
    mockGetApprovals.mockResolvedValue([approval()]);
    mockGetDecisions.mockResolvedValue([decision()]);
    mockGetHistory.mockResolvedValue(historyResponse([{ strategy: strategyItem("STR-1"), revision: null }]));
    render(<StrategyRevisionSection campaignId="campaign-1" currentStrategyId="STR-1" role="OWNER" onRevised={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("Registrar revisión de estrategia")).toBeInTheDocument());
  });
});

describe("StrategyRevisionSection — application authority", () => {
  it("MEMBER never sees the revise control, even with an eligible approval", async () => {
    mockGetApprovals.mockResolvedValue([approval()]);
    mockGetDecisions.mockResolvedValue([decision()]);
    mockGetHistory.mockResolvedValue(historyResponse([{ strategy: strategyItem("STR-1"), revision: null }]));
    render(<StrategyRevisionSection campaignId="campaign-1" currentStrategyId="STR-1" role="MEMBER" onRevised={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("Revisión estratégica gobernada")).toBeInTheDocument());
    expect(screen.queryByText("Registrar revisión de estrategia")).not.toBeInTheDocument();
  });
});

describe("StrategyRevisionSection — submission", () => {
  it("OWNER can submit a revision with the selected approval and complete state", async () => {
    mockGetApprovals.mockResolvedValueOnce([approval()]);
    mockGetDecisions.mockResolvedValueOnce([decision()]);
    mockGetHistory.mockResolvedValueOnce(historyResponse([{ strategy: strategyItem("STR-1"), revision: null }]));

    const revised: StrategyRevisionResult = {
      strategy: strategyItem("STR-2", { version: 2, origin: "REVISION", summary: "New summary." }),
      positioning: { id: "POS-2", statement: "New positioning.", created_at: "2026-01-05T00:00:00Z" },
      revision: {
        id: "SRV-1", campaign_id: "campaign-1", strategic_approval_id: "SAP-1",
        base_strategy_id: "STR-1", result_strategy_id: "STR-2", created_at: "2026-01-05T00:00:00Z",
      },
    };
    mockRevise.mockResolvedValue(revised);
    mockGetApprovals.mockResolvedValueOnce([approval()]);
    mockGetDecisions.mockResolvedValueOnce([decision()]);
    mockGetHistory.mockResolvedValueOnce(
      historyResponse([
        { strategy: strategyItem("STR-1"), revision: null },
        { strategy: revised.strategy, revision: revised.revision },
      ]),
    );

    const onRevised = vi.fn();
    render(<StrategyRevisionSection campaignId="campaign-1" currentStrategyId="STR-1" role="OWNER" onRevised={onRevised} />);

    await waitFor(() => expect(screen.getByText("Registrar revisión de estrategia")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Registrar revisión de estrategia"));

    await userEvent.selectOptions(screen.getByLabelText("Aprobación estratégica que autoriza esta revisión"), "SAP-1");
    await userEvent.type(screen.getByLabelText("Nuevo resumen de estrategia"), "New summary.");
    await userEvent.type(screen.getByLabelText("Nuevo posicionamiento"), "New positioning.");
    await userEvent.click(screen.getByText("Confirmar revisión"));

    await waitFor(() =>
      expect(mockRevise).toHaveBeenCalledWith("campaign-1", "STR-1", "SAP-1", "New summary.", "New positioning."),
    );
    await waitFor(() => expect(onRevised).toHaveBeenCalled());
  });

  it("surfaces a mutation error without losing the form", async () => {
    mockGetApprovals.mockResolvedValue([approval()]);
    mockGetDecisions.mockResolvedValue([decision()]);
    mockGetHistory.mockResolvedValue(historyResponse([{ strategy: strategyItem("STR-1"), revision: null }]));
    mockRevise.mockRejectedValue(new ApiError(409, "STRATEGIC_APPROVAL_ALREADY_CONSUMED", "Already consumed."));

    render(<StrategyRevisionSection campaignId="campaign-1" currentStrategyId="STR-1" role="OWNER" onRevised={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("Registrar revisión de estrategia")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Registrar revisión de estrategia"));
    await userEvent.selectOptions(screen.getByLabelText("Aprobación estratégica que autoriza esta revisión"), "SAP-1");
    await userEvent.type(screen.getByLabelText("Nuevo resumen de estrategia"), "x");
    await userEvent.type(screen.getByLabelText("Nuevo posicionamiento"), "y");
    await userEvent.click(screen.getByText("Confirmar revisión"));

    await waitFor(() => expect(screen.getByText("No pudimos completar esta acción. Intenta de nuevo.")).toBeInTheDocument());
    expect(screen.getByLabelText("Nuevo resumen de estrategia")).toBeInTheDocument();
  });
});

describe("StrategyRevisionSection — historical provenance", () => {
  it("shows historical versions with revision provenance, excluding the current one", async () => {
    mockGetApprovals.mockResolvedValue([]);
    mockGetDecisions.mockResolvedValue([]);
    mockGetHistory.mockResolvedValue(
      historyResponse([
        { strategy: strategyItem("STR-1", { summary: "First draft." }), revision: null },
        {
          strategy: strategyItem("STR-2", { version: 2, origin: "REVISION", summary: "Revised strategy." }),
          revision: {
            id: "SRV-1", campaign_id: "campaign-1", strategic_approval_id: "SAP-1",
            base_strategy_id: "STR-1", result_strategy_id: "STR-2", created_at: "2026-01-04T00:00:00Z",
          },
        },
      ]),
    );
    render(<StrategyRevisionSection campaignId="campaign-1" currentStrategyId="STR-2" role="OWNER" onRevised={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/Historial de versiones/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/Historial de versiones/));
    expect(screen.getByText("First draft.")).toBeInTheDocument();
    expect(screen.queryByText("Revised strategy.")).not.toBeInTheDocument(); // current, not in history list
  });
});
