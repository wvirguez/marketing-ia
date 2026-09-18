import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrategicDecisionSection } from "@/components/campaigns/detail/strategic-decision-section";
import { ApiError } from "@/lib/api/client";
import type { StrategicApprovalPublic } from "@/types/strategic-approvals";
import type { StrategicDecisionPublic } from "@/types/strategic-decisions";

vi.mock("@/lib/api/strategic-decisions", () => ({
  getStrategicDecisions: vi.fn(),
  recordStrategicDecision: vi.fn(),
  supersedeStrategicDecision: vi.fn(),
}));

vi.mock("@/lib/api/strategic-approvals", () => ({
  getStrategicApprovals: vi.fn(),
  recordStrategicApproval: vi.fn(),
}));

import {
  getStrategicDecisions,
  recordStrategicDecision,
  supersedeStrategicDecision,
} from "@/lib/api/strategic-decisions";
import { getStrategicApprovals, recordStrategicApproval } from "@/lib/api/strategic-approvals";

const mockGetDecisions = vi.mocked(getStrategicDecisions);
const mockRecord = vi.mocked(recordStrategicDecision);
const mockSupersede = vi.mocked(supersedeStrategicDecision);
const mockGetApprovals = vi.mocked(getStrategicApprovals);
const mockRecordApproval = vi.mocked(recordStrategicApproval);

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

beforeEach(() => {
  vi.clearAllMocks();
  mockGetApprovals.mockResolvedValue([]);
});

describe("StrategicDecisionSection — fetch / render", () => {
  it("filters the campaign-wide list to only this Recommendation's own decisions", async () => {
    mockGetDecisions.mockResolvedValue([
      decision({ id: "DEC-1", strategic_recommendation_candidate_id: "SRC-1" }),
      decision({ id: "DEC-2", strategic_recommendation_candidate_id: "SRC-OTHER" }),
    ]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="OWNER" />);
    await waitFor(() => expect(screen.getByText(/Adopt the shorter-hooks direction\./)).toBeInTheDocument());
    expect(screen.queryByText("DEC-2")).not.toBeInTheDocument();
  });

  it("shows a neutral message when no decision has been recorded yet", async () => {
    mockGetDecisions.mockResolvedValue([]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="OWNER" />);
    await waitFor(() =>
      expect(screen.getByText(/Aún no se ha registrado una decisión estratégica/)).toBeInTheDocument(),
    );
  });

  it("shows an error state with retry on a failed fetch", async () => {
    mockGetDecisions.mockRejectedValueOnce(new ApiError(500, "SERVER_ERROR", "boom"));
    mockGetDecisions.mockResolvedValueOnce([]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="OWNER" />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Reintentar"));
    await waitFor(() =>
      expect(screen.getByText(/Aún no se ha registrado una decisión estratégica/)).toBeInTheDocument(),
    );
  });
});

describe("StrategicDecisionSection — application authority", () => {
  it("MEMBER sees status but no mutation control", async () => {
    mockGetDecisions.mockResolvedValue([]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="MEMBER" />);
    await waitFor(() =>
      expect(screen.getByText(/Aún no se ha registrado una decisión estratégica/)).toBeInTheDocument(),
    );
    expect(screen.queryByText("Registrar decisión")).not.toBeInTheDocument();
  });

  it("OWNER can record the first decision", async () => {
    mockGetDecisions.mockResolvedValueOnce([]);
    mockRecord.mockResolvedValue(decision());
    mockGetDecisions.mockResolvedValueOnce([decision()]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="OWNER" />);

    await waitFor(() => expect(screen.getByLabelText("Justificación")).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText("Justificación"), "Adopt the direction.");
    await userEvent.click(screen.getByText("Registrar decisión"));

    await waitFor(() =>
      expect(mockRecord).toHaveBeenCalledWith("campaign-1", "SRC-1", "ADOPT", "Adopt the direction."),
    );
    await waitFor(() => expect(screen.getByText(/Adopt the shorter-hooks direction\./)).toBeInTheDocument());
  });

  it("ADMIN can supersede the current decision", async () => {
    mockGetDecisions.mockResolvedValueOnce([decision()]);
    const replacement = decision({ id: "DEC-2", decision_type: "DEFER", statement: "Defer instead." });
    mockSupersede.mockResolvedValue(replacement);
    mockGetDecisions.mockResolvedValueOnce([
      { ...decision(), current: false, superseded_by_strategic_decision_id: "DEC-2" },
      replacement,
    ]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="ADMIN" />);

    await waitFor(() => expect(screen.getByText("Reemplazar esta decisión")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Reemplazar esta decisión"));
    await userEvent.type(screen.getByLabelText("Justificación"), "Defer instead.");
    await userEvent.click(screen.getByText("Confirmar reemplazo"));

    await waitFor(() => expect(mockSupersede).toHaveBeenCalledWith("campaign-1", "DEC-1", "ADOPT", "Defer instead."));
    await waitFor(() => expect(screen.getByText(/Defer instead\./)).toBeInTheDocument());
  });

  it("shows the historical decision once superseded", async () => {
    const superseded = decision({ current: false, superseded_at: "2026-01-02T00:00:00Z", superseded_by_strategic_decision_id: "DEC-2" });
    const current = decision({ id: "DEC-2", decision_type: "DEFER", statement: "Defer instead." });
    mockGetDecisions.mockResolvedValue([superseded, current]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="OWNER" />);

    await waitFor(() => expect(screen.getByText(/Defer instead\./)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/Historial de decisiones/));
    expect(screen.getByText("Adopt the shorter-hooks direction.")).toBeInTheDocument();
  });

  it("surfaces a mutation error without losing the current decision", async () => {
    mockGetDecisions.mockResolvedValue([]);
    mockRecord.mockRejectedValue(new ApiError(409, "STRATEGIC_RECOMMENDATION_NOT_ACCEPTED", "Not accepted yet."));
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="OWNER" />);

    await waitFor(() => expect(screen.getByLabelText("Justificación")).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText("Justificación"), "x");
    await userEvent.click(screen.getByText("Registrar decisión"));

    // describeCampaignError has no bespoke case for this domain code (like
    // every other domain-specific 409 in this app), so it maps to the
    // generic client-error message — the decision form remains visible,
    // never silently cleared.
    await waitFor(() => expect(screen.getByText("No pudimos completar esta acción. Intenta de nuevo.")).toBeInTheDocument());
    expect(screen.getByLabelText("Justificación")).toBeInTheDocument();
  });
});

describe("StrategicDecisionSection — StrategicApproval (MVP-29B)", () => {
  it("shows no approval registered when the current ADOPT decision has none", async () => {
    mockGetDecisions.mockResolvedValue([decision()]);
    mockGetApprovals.mockResolvedValue([]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="MEMBER" />);
    await waitFor(() => expect(screen.getByText("Sin aprobación estratégica registrada.")).toBeInTheDocument());
  });

  it("displays the recorded approval outcome for the current decision", async () => {
    mockGetDecisions.mockResolvedValue([decision()]);
    mockGetApprovals.mockResolvedValue([approval({ strategic_decision_id: "DEC-1", outcome: "APPROVED" })]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="OWNER" />);
    await waitFor(() => expect(screen.getByText(/Aprobación estratégica: Aprobada/)).toBeInTheDocument());
    // Already-approved: no Aprobar/Rechazar controls remain.
    expect(screen.queryByText("Aprobar")).not.toBeInTheDocument();
  });

  it("OWNER/ADMIN see Aprobar/Rechazar controls for a current ADOPT decision without an approval", async () => {
    mockGetDecisions.mockResolvedValue([decision({ decision_type: "ADOPT" })]);
    mockGetApprovals.mockResolvedValue([]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="ADMIN" />);
    await waitFor(() => expect(screen.getByText("Aprobar")).toBeInTheDocument());
    expect(screen.getByText("Rechazar")).toBeInTheDocument();
  });

  it("MEMBER never sees Aprobar/Rechazar controls", async () => {
    mockGetDecisions.mockResolvedValue([decision({ decision_type: "ADOPT" })]);
    mockGetApprovals.mockResolvedValue([]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="MEMBER" />);
    await waitFor(() => expect(screen.getByText("Sin aprobación estratégica registrada.")).toBeInTheDocument());
    expect(screen.queryByText("Aprobar")).not.toBeInTheDocument();
    expect(screen.queryByText("Rechazar")).not.toBeInTheDocument();
  });

  it("DEFER/DECLINE decisions never show Aprobar/Rechazar controls, even for OWNER", async () => {
    mockGetDecisions.mockResolvedValue([decision({ decision_type: "DEFER" })]);
    mockGetApprovals.mockResolvedValue([]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="OWNER" />);
    await waitFor(() => expect(screen.getByText("Sin aprobación estratégica registrada.")).toBeInTheDocument());
    expect(screen.queryByText("Aprobar")).not.toBeInTheDocument();
  });

  it("OWNER can record an approval, which then displays and hides the controls", async () => {
    mockGetDecisions.mockResolvedValue([decision({ decision_type: "ADOPT" })]);
    mockGetApprovals.mockResolvedValueOnce([]);
    mockRecordApproval.mockResolvedValue(approval({ outcome: "APPROVED" }));
    mockGetApprovals.mockResolvedValueOnce([approval({ outcome: "APPROVED" })]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="OWNER" />);

    await waitFor(() => expect(screen.getByText("Aprobar")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Aprobar"));

    await waitFor(() => expect(mockRecordApproval).toHaveBeenCalledWith("campaign-1", "DEC-1", "APPROVED"));
    await waitFor(() => expect(screen.getByText(/Aprobación estratégica: Aprobada/)).toBeInTheDocument());
    expect(screen.queryByText("Aprobar")).not.toBeInTheDocument();
  });

  it("historical Approval stays attached to its own historical Decision, and the new current Decision never inherits it", async () => {
    const superseded = decision({
      id: "DEC-1", current: false, superseded_at: "2026-01-02T00:00:00Z", superseded_by_strategic_decision_id: "DEC-2",
    });
    const current = decision({ id: "DEC-2", decision_type: "ADOPT", statement: "New direction." });
    mockGetDecisions.mockResolvedValue([superseded, current]);
    mockGetApprovals.mockResolvedValue([approval({ strategic_decision_id: "DEC-1", outcome: "APPROVED" })]);
    render(<StrategicDecisionSection campaignId="campaign-1" recommendationId="SRC-1" role="OWNER" />);

    // Current decision (DEC-2) has no approval of its own — never inherits DEC-1's.
    await waitFor(() => expect(screen.getByText("Sin aprobación estratégica registrada.")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("Aprobar")).toBeInTheDocument());

    // The historical decision (DEC-1) still shows its own approval once expanded.
    await userEvent.click(screen.getByText(/Historial de decisiones/));
    expect(screen.getByText(/Aprobación estratégica: Aprobada/)).toBeInTheDocument();
  });
});
