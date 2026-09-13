import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LearningPanel } from "@/components/campaigns/detail/learning-panel";
import { CampaignDetail } from "@/components/campaigns/detail/campaign-detail";
import { ApiError } from "@/lib/api/client";
import type { LearningCandidatePublic, LearningCandidateStatus, LearningResponse } from "@/types/learning";

vi.mock("@/lib/api/learning", () => ({
  getCampaignLearning: vi.fn(),
  deriveCampaignLearning: vi.fn(),
}));
vi.mock("@/lib/api/campaigns", () => ({
  getCampaign: vi.fn(),
  listCampaignRuns: vi.fn(),
}));

import { deriveCampaignLearning, getCampaignLearning } from "@/lib/api/learning";
import { getCampaign, listCampaignRuns } from "@/lib/api/campaigns";

const mockGetCampaignLearning = vi.mocked(getCampaignLearning);
const mockDeriveCampaignLearning = vi.mocked(deriveCampaignLearning);
const mockGetCampaign = vi.mocked(getCampaign);
const mockListCampaignRuns = vi.mocked(listCampaignRuns);

beforeEach(() => {
  vi.clearAllMocks();
});

const NEUTRAL_EMPTY_TITLE = "Aún no hay candidatos de aprendizaje para esta campaña.";
const COMPLETED_EMPTY_TITLE = "El proceso se completó, pero no se generaron candidatos de aprendizaje.";
const CTA_LABEL = "Generar candidatos de aprendizaje";

const STATUS_LABELS: Record<LearningCandidateStatus, string> = {
  CANDIDATE_IDENTIFIED: "Candidato de aprendizaje",
  PROVISIONAL: "Aprendizaje provisional",
  VALIDATION_PENDING: "Pendiente de validación",
  VALIDATED: "Aprendizaje validado",
  REJECTED: "Rechazado",
  INSUFFICIENT_EVIDENCE: "Evidencia insuficiente",
};

function emptyLearning(): LearningResponse {
  return { learning_candidates: [], strategic_recommendation_candidates: [] };
}

function makeCandidate(overrides: Partial<LearningCandidatePublic> = {}): LearningCandidatePublic {
  return {
    id: "LRN-1",
    analysis_result_id: "ANL-1",
    status: "CANDIDATE_IDENTIFIED",
    summary: "clicks increased for Instagram: 100 to 150.",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

async function clickDerive(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /generar candidatos de aprendizaje|generando/i }));
}

describe("LearningPanel — fetch / render", () => {
  it("does not fetch while inactive", () => {
    render(<LearningPanel campaignId="campaign-1" active={false} refreshToken={0} />);
    expect(mockGetCampaignLearning).not.toHaveBeenCalled();
  });

  it("loads GET /learning once activated", async () => {
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(mockGetCampaignLearning).toHaveBeenCalledWith("campaign-1"));
  });

  it("renders the loading state", () => {
    mockGetCampaignLearning.mockImplementation(() => new Promise(() => {}));
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(screen.getByText("Cargando aprendizajes…")).toBeInTheDocument();
  });

  it("shows the neutral empty state and the derive CTA when there are no candidates", async () => {
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByText(NEUTRAL_EMPTY_TITLE)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: CTA_LABEL })).toBeInTheDocument();
  });

  it("never claims metrics or analysis are missing in the collapsed empty state", async () => {
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);
    expect(screen.queryByText(/no hay métricas/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/no hay análisis/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/no existe un resultado de análisis/i)).not.toBeInTheDocument();
  });

  it("renders CANDIDATE_IDENTIFIED as 'Candidato de aprendizaje'", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ status: "CANDIDATE_IDENTIFIED" })],
      strategic_recommendation_candidates: [],
    });
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByText("Candidato de aprendizaje")).toBeInTheDocument();
  });

  it("never renders 'Aprendizaje validado' for a CANDIDATE_IDENTIFIED candidate", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ status: "CANDIDATE_IDENTIFIED" })],
      strategic_recommendation_candidates: [],
    });
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Candidato de aprendizaje");
    expect(screen.queryByText("Aprendizaje validado")).not.toBeInTheDocument();
    expect(screen.queryByText(/hallazgo/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/conclusión validada/i)).not.toBeInTheDocument();
  });

  it.each(Object.entries(STATUS_LABELS) as [LearningCandidateStatus, string][])(
    "maps status %s to the exact label %s",
    async (status, label) => {
      mockGetCampaignLearning.mockResolvedValue({
        learning_candidates: [makeCandidate({ id: `LRN-${status}`, status })],
        strategic_recommendation_candidates: [],
      });
      render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
      expect(await screen.findByText(label)).toBeInTheDocument();
    },
  );

  it("renders the candidate summary verbatim", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ summary: "CTR increased for TikTok: 2.1 to 3.4." })],
      strategic_recommendation_candidates: [],
    });
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByText("CTR increased for TikTok: 2.1 to 3.4.")).toBeInTheDocument();
  });

  it("never renders the analysis_result_id or any raw internal identifier", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", analysis_result_id: "ANL-99" })],
      strategic_recommendation_candidates: [],
    });
    const { container } = render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Candidato de aprendizaje");
    expect(container.textContent).not.toContain("ANL-99");
    expect(container.textContent).not.toContain("LRN-1");
  });
});

describe("LearningPanel — derive", () => {
  it("calls POST only after an explicit click, never on mount or activation", async () => {
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);
    expect(mockDeriveCampaignLearning).not.toHaveBeenCalled();
  });

  it("clicking the CTA calls deriveCampaignLearning exactly once with the campaign id and no body", async () => {
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());
    mockDeriveCampaignLearning.mockResolvedValue(emptyLearning());
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickDerive(user);
    await waitFor(() => expect(mockDeriveCampaignLearning).toHaveBeenCalledTimes(1));
    // Exactly one argument (the campaign id) — proves no body/payload of any
    // kind (no client_request_id, no {}, no null) is ever passed through.
    expect(mockDeriveCampaignLearning).toHaveBeenCalledWith("campaign-1");
    expect(mockDeriveCampaignLearning.mock.calls[0]).toHaveLength(1);
  });

  it("disables the button and sets aria-busy while the request is pending", async () => {
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());
    let resolveDerive: (value: LearningResponse) => void = () => {};
    mockDeriveCampaignLearning.mockImplementation(() => new Promise((resolve) => { resolveDerive = resolve; }));
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickDerive(user);
    const pendingButton = await screen.findByRole("button", { name: /generando/i });
    expect(pendingButton).toBeDisabled();
    expect(pendingButton).toHaveAttribute("aria-busy", "true");

    resolveDerive(emptyLearning());
    await waitFor(() => expect(screen.getByRole("button", { name: CTA_LABEL })).not.toBeDisabled());
  });

  it("prevents a duplicate derive call while one is already pending", async () => {
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());
    let resolveDerive: (value: LearningResponse) => void = () => {};
    mockDeriveCampaignLearning.mockImplementation(() => new Promise((resolve) => { resolveDerive = resolve; }));
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickDerive(user);
    const pendingButton = await screen.findByRole("button", { name: /generando/i });
    await user.click(pendingButton);
    resolveDerive(emptyLearning());

    await waitFor(() => expect(screen.getByRole("button", { name: CTA_LABEL })).not.toBeDisabled());
    expect(mockDeriveCampaignLearning).toHaveBeenCalledTimes(1);
  });

  it("replaces the local candidate list with the derive response's own candidates", async () => {
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());
    mockDeriveCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ summary: "New candidate from derive." })],
      strategic_recommendation_candidates: [],
    });
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickDerive(user);
    expect(await screen.findByText("New candidate from derive.")).toBeInTheDocument();
  });

  it("does not visually duplicate a candidate returned again by a later derive", async () => {
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());
    mockDeriveCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ summary: "Same candidate every time." })],
      strategic_recommendation_candidates: [],
    });
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickDerive(user);
    await screen.findByText("Same candidate every time.");
    await waitFor(() => expect(screen.getByRole("button", { name: CTA_LABEL })).not.toBeDisabled());

    await clickDerive(user);
    await waitFor(() => expect(mockDeriveCampaignLearning).toHaveBeenCalledTimes(2));
    expect(screen.getAllByText("Same candidate every time.")).toHaveLength(1);
  });

  it("shows the session-known completed-empty copy when derive succeeds with zero candidates", async () => {
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());
    mockDeriveCampaignLearning.mockResolvedValue(emptyLearning());
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickDerive(user);
    expect(await screen.findByText(COMPLETED_EMPTY_TITLE)).toBeInTheDocument();
    expect(screen.queryByText(NEUTRAL_EMPTY_TITLE)).not.toBeInTheDocument();
  });

  it("preserves previously rendered candidates when derive fails", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ summary: "Prior candidate stays visible." })],
      strategic_recommendation_candidates: [],
    });
    mockDeriveCampaignLearning.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Prior candidate stays visible.");

    await clickDerive(user);
    await waitFor(() => expect(mockDeriveCampaignLearning).toHaveBeenCalledTimes(1));
    expect(screen.getByText("Prior candidate stays visible.")).toBeInTheDocument();
  });

  it("shows an accessible inline error when derive fails", async () => {
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());
    mockDeriveCampaignLearning.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickDerive(user);
    const alert = await screen.findByRole("alert");
    expect(alert).toBeInTheDocument();
  });

  it("does not issue a redundant GET refetch after a successful derive", async () => {
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());
    mockDeriveCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate()],
      strategic_recommendation_candidates: [],
    });
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickDerive(user);
    await screen.findByText("Candidato de aprendizaje");
    expect(mockGetCampaignLearning).toHaveBeenCalledTimes(1);
  });
});

describe("LearningPanel — GET failure", () => {
  it("shows an error state with a retry affordance when the initial load fails", async () => {
    mockGetCampaignLearning.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByRole("button", { name: "Reintentar" })).toBeInTheDocument();
  });

  it("retrying invokes getCampaignLearning again", async () => {
    mockGetCampaignLearning.mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom"));
    mockGetCampaignLearning.mockResolvedValueOnce(emptyLearning());
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    const retryButton = await screen.findByRole("button", { name: "Reintentar" });

    await user.click(retryButton);
    await waitFor(() => expect(mockGetCampaignLearning).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(NEUTRAL_EMPTY_TITLE)).toBeInTheDocument();
  });
});

describe("LearningPanel — strategic recommendation boundary", () => {
  it("renders no recommendation section even when the response includes one", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ status: "VALIDATED" })],
      strategic_recommendation_candidates: [
        {
          id: "SRC-1",
          learning_candidate_id: "LRN-1",
          summary: "Shift creative brief toward shorter hooks.",
          decision: null,
          created_at: "2026-01-01T00:00:00Z",
          decided_at: null,
        },
      ],
    });
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Aprendizaje validado");
    expect(screen.queryByText("Shift creative brief toward shorter hooks.")).not.toBeInTheDocument();
  });

  it("renders no Aceptar/Rechazar decision controls", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [],
      strategic_recommendation_candidates: [
        {
          id: "SRC-1",
          learning_candidate_id: "LRN-1",
          summary: "Shift creative brief toward shorter hooks.",
          decision: null,
          created_at: "2026-01-01T00:00:00Z",
          decided_at: null,
        },
      ],
    });
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);
    expect(screen.queryByRole("button", { name: /aceptar/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /rechazar/i })).not.toBeInTheDocument();
  });
});

describe("LearningPanel integration inside CampaignDetail", () => {
  it("registers the Aprendizajes tab immediately after Métricas, with every earlier tab in its original order", async () => {
    mockGetCampaign.mockResolvedValue({
      id: "campaign-42",
      name: "Campaña de prueba",
      status: "DRAFT",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      archived_at: null,
    });
    mockListCampaignRuns.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());

    render(<CampaignDetail campaignId="campaign-42" />);
    await screen.findByRole("heading", { name: "Campaña de prueba" });

    const tabLabels = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(tabLabels).toEqual([
      "Resumen",
      "Investigación",
      "Audiencia",
      "Estrategia",
      "Plan",
      "Contenido",
      "Creatividades",
      "Tracking",
      "Métricas",
      "Aprendizajes",
    ]);
  });

  it("loads GET /learning only once the Aprendizajes tab is activated", async () => {
    mockGetCampaign.mockResolvedValue({
      id: "campaign-42",
      name: "Campaña de prueba",
      status: "DRAFT",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      archived_at: null,
    });
    mockListCampaignRuns.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    mockGetCampaignLearning.mockResolvedValue(emptyLearning());

    const user = userEvent.setup();
    render(<CampaignDetail campaignId="campaign-42" />);
    await screen.findByRole("heading", { name: "Campaña de prueba" });
    expect(mockGetCampaignLearning).not.toHaveBeenCalled();

    await user.click(screen.getByRole("tab", { name: "Aprendizajes" }));
    expect(await screen.findByText(NEUTRAL_EMPTY_TITLE)).toBeInTheDocument();
    expect(mockGetCampaignLearning).toHaveBeenCalledWith("campaign-42");
  });
});
