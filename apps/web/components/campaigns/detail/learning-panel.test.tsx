import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LearningPanel } from "@/components/campaigns/detail/learning-panel";
import { CampaignDetail } from "@/components/campaigns/detail/campaign-detail";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/auth-context";
import type { AuthContextValue } from "@/lib/auth/auth-context";
import type { LearningQualificationPublic, LearningCandidatePublic, LearningCandidateStatus, LearningResponse, StrategicRecommendationCandidatePublic } from "@/types/learning";

vi.mock("@/lib/api/learning", () => ({
  updateLearningQualification: vi.fn(),
  attachLearningEvidence: vi.fn(),
  disposeLearningEvidence: vi.fn(),
  getCampaignLearning: vi.fn(),
  deriveCampaignLearning: vi.fn(),
  markLearningCandidateProvisional: vi.fn(),
  markLearningCandidateValidationPending: vi.fn(),
  decideLearningCandidate: vi.fn(),
  createStrategicRecommendation: vi.fn(),
  decideStrategicRecommendation: vi.fn(),
}));
vi.mock("@/lib/api/campaigns", () => ({
  getCampaign: vi.fn(),
  listCampaignRuns: vi.fn(),
}));
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: vi.fn(),
}));

import {
  updateLearningQualification,
  attachLearningEvidence,
  disposeLearningEvidence,
  createStrategicRecommendation,
  decideLearningCandidate,
  decideStrategicRecommendation,
  deriveCampaignLearning,
  getCampaignLearning,
  markLearningCandidateProvisional,
  markLearningCandidateValidationPending,
} from "@/lib/api/learning";
import { getCampaign, listCampaignRuns } from "@/lib/api/campaigns";

const mockGetCampaignLearning = vi.mocked(getCampaignLearning);
const mockDeriveCampaignLearning = vi.mocked(deriveCampaignLearning);
const mockMarkProvisional = vi.mocked(markLearningCandidateProvisional);
const mockMarkValidationPending = vi.mocked(markLearningCandidateValidationPending);
const mockDecideLearningCandidate = vi.mocked(decideLearningCandidate);
const mockCreateStrategicRecommendation = vi.mocked(createStrategicRecommendation);
const mockDecideStrategicRecommendation = vi.mocked(decideStrategicRecommendation);
const mockGetCampaign = vi.mocked(getCampaign);
const mockListCampaignRuns = vi.mocked(listCampaignRuns);
const mockUseAuth = vi.mocked(useAuth);

function makeAuth(role: string | null = "OWNER"): AuthContextValue {
  if (role === null) {
    return { status: "unauthenticated", login: vi.fn(), register: vi.fn(), logout: vi.fn(), refresh: vi.fn() };
  }
  return {
    status: "authenticated",
    session: {
      user: { id: "USR-1", email: "real.user@impulso.test", display_name: "Real User", status: "ACTIVE", preferences: { locale: null, timezone: null } },
      workspace: { id: "WS-1", name: "Real Workspace", slug: "real-workspace" },
      membership: { role },
    },
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn().mockResolvedValue(undefined),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // Defaults to OWNER so every pre-existing test (written before MVP-23B's
  // role gating existed) keeps seeing every action it already expected —
  // tests that care about MEMBER-vs-OWNER/ADMIN visibility set this
  // explicitly (mockUseAuth.mockReturnValue(makeAuth("MEMBER"))).
  mockUseAuth.mockReturnValue(makeAuth("OWNER"));
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
    qualification: overrides.status === "VALIDATION_PENDING" ? makeQualification() : null,
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

function makeRecommendation(overrides: Partial<StrategicRecommendationCandidatePublic> = {}): StrategicRecommendationCandidatePublic {
  return {
    id: "SRC-1",
    learning_candidate_id: "LRN-1",
    summary: "Shift creative brief toward shorter hooks.",
    decision: null,
    created_at: "2026-01-01T00:00:00Z",
    decided_at: null,
    ...overrides,
  };
}

describe("LearningPanel — strategic recommendation display (MVP-23B)", () => {
  it("renders an existing recommendation from the GET response, associated with its own candidate", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", status: "VALIDATED" })],
      strategic_recommendation_candidates: [makeRecommendation({ learning_candidate_id: "LRN-1" })],
    });
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Aprendizaje validado");
    expect(await screen.findByText("Shift creative brief toward shorter hooks.")).toBeInTheDocument();
  });

  it("renders Aceptar/Rechazar for an undecided recommendation only for OWNER/ADMIN", async () => {
    mockUseAuth.mockReturnValue(makeAuth("MEMBER"));
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", status: "VALIDATED" })],
      strategic_recommendation_candidates: [makeRecommendation({ learning_candidate_id: "LRN-1" })],
    });
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Shift creative brief toward shorter hooks.");
    expect(screen.queryByRole("button", { name: "Aceptar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rechazar" })).not.toBeInTheDocument();
  });

  it("never shows decision controls for an already-decided recommendation", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", status: "VALIDATED" })],
      strategic_recommendation_candidates: [makeRecommendation({ learning_candidate_id: "LRN-1", decision: "ACCEPTED", decided_at: "2026-01-02T00:00:00Z" })],
    });
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Shift creative brief toward shorter hooks.");
    expect(screen.queryByRole("button", { name: "Aceptar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rechazar" })).not.toBeInTheDocument();
    expect(screen.getByText("Recomendación aceptada")).toBeInTheDocument();
  });
});

describe("LearningPanel — candidate maturation actions (MVP-23B)", () => {
  it("shows 'Marcar como provisional' for CANDIDATE_IDENTIFIED and calls the route on click", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", status: "CANDIDATE_IDENTIFIED" })],
      strategic_recommendation_candidates: [],
    });
    mockMarkProvisional.mockResolvedValue(makeCandidate({ id: "LRN-1", status: "PROVISIONAL" }));
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await user.click(await screen.findByRole("button", { name: "Marcar como provisional" }));
    expect(mockMarkProvisional).toHaveBeenCalledWith("campaign-1", "LRN-1");
    expect(await screen.findByText("Aprendizaje provisional")).toBeInTheDocument();
  });

  it("shows 'Enviar a validación' for PROVISIONAL and 'Volver a enviar a validación' for INSUFFICIENT_EVIDENCE", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [
        makeCandidate({ id: "LRN-1", status: "PROVISIONAL" }),
        makeCandidate({ id: "LRN-2", status: "INSUFFICIENT_EVIDENCE" }),
      ],
      strategic_recommendation_candidates: [],
    });
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByRole("button", { name: "Enviar a validación" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Volver a enviar a validación" })).toBeInTheDocument();
  });

  it("MEMBER can reopen INSUFFICIENT_EVIDENCE (MVP-23A-R1) without asserting new evidence exists", async () => {
    mockUseAuth.mockReturnValue(makeAuth("MEMBER"));
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", status: "INSUFFICIENT_EVIDENCE" })],
      strategic_recommendation_candidates: [],
    });
    mockMarkValidationPending.mockResolvedValue(makeCandidate({ id: "LRN-1", status: "VALIDATION_PENDING" }));
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await user.click(await screen.findByRole("button", { name: "Volver a enviar a validación" }));
    expect(mockMarkValidationPending).toHaveBeenCalledWith("campaign-1", "LRN-1");
    expect(await screen.findByText("Pendiente de validación")).toBeInTheDocument();
  });

  it("MEMBER does not see final Learning decision controls for VALIDATION_PENDING", async () => {
    mockUseAuth.mockReturnValue(makeAuth("MEMBER"));
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", status: "VALIDATION_PENDING" })],
      strategic_recommendation_candidates: [],
    });
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Pendiente de validación");
    expect(screen.queryByRole("button", { name: "Validar aprendizaje" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /rechazar/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Evidencia insuficiente" })).not.toBeInTheDocument();
  });

  it("OWNER sees final Learning decision controls, requires a second click to confirm, and applies the server response", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", status: "VALIDATION_PENDING" })],
      strategic_recommendation_candidates: [],
    });
    mockDecideLearningCandidate.mockResolvedValue(makeCandidate({ id: "LRN-1", status: "VALIDATED" }));
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    const validateButton = await screen.findByRole("button", { name: "Validar aprendizaje" });

    await user.click(validateButton);
    expect(mockDecideLearningCandidate).not.toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "Confirmar" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Confirmar" }));
    expect(mockDecideLearningCandidate).toHaveBeenCalledWith("campaign-1", "LRN-1", "VALIDATED");
    expect(await screen.findByText("Aprendizaje validado")).toBeInTheDocument();
  });

  it("ADMIN can also decide, and ADMIN is distinct from OWNER but both pass the gate", async () => {
    mockUseAuth.mockReturnValue(makeAuth("ADMIN"));
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", status: "VALIDATION_PENDING" })],
      strategic_recommendation_candidates: [],
    });
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByRole("button", { name: "Validar aprendizaje" })).toBeInTheDocument();
  });

  it("a mutation failure leaves the prior candidate status untouched and shows an accessible error", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", status: "CANDIDATE_IDENTIFIED" })],
      strategic_recommendation_candidates: [],
    });
    mockMarkProvisional.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await user.click(await screen.findByRole("button", { name: "Marcar como provisional" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Candidato de aprendizaje")).toBeInTheDocument();
  });

  it("single-flight: a second click while a mutation is pending does not issue a second request", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", status: "CANDIDATE_IDENTIFIED" })],
      strategic_recommendation_candidates: [],
    });
    let resolveMark: (value: LearningCandidatePublic) => void = () => {};
    mockMarkProvisional.mockImplementation(() => new Promise((resolve) => { resolveMark = resolve; }));
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    const button = await screen.findByRole("button", { name: "Marcar como provisional" });

    await user.click(button);
    await user.click(button);
    resolveMark(makeCandidate({ id: "LRN-1", status: "PROVISIONAL" }));
    await waitFor(() => expect(mockMarkProvisional).toHaveBeenCalledTimes(1));
  });
});

describe("LearningPanel — strategic recommendation creation (MVP-23B)", () => {
  it("shows a recommendation form only for VALIDATED, and MEMBER may submit it", async () => {
    mockUseAuth.mockReturnValue(makeAuth("MEMBER"));
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", status: "VALIDATED" })],
      strategic_recommendation_candidates: [],
    });
    mockCreateStrategicRecommendation.mockResolvedValue(makeRecommendation({ id: "SRC-9", learning_candidate_id: "LRN-1", summary: "Try shorter hooks." }));
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    const textarea = await screen.findByLabelText("Proponer una recomendación estratégica a partir de este aprendizaje validado");
    await user.type(textarea, "Try shorter hooks.");
    await user.click(screen.getByRole("button", { name: "Proponer recomendación" }));
    expect(mockCreateStrategicRecommendation).toHaveBeenCalledWith("campaign-1", "LRN-1", "Try shorter hooks.");
    expect(await screen.findByText("Try shorter hooks.")).toBeInTheDocument();
  });

  it("does not show a recommendation form for a non-VALIDATED candidate", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", status: "CANDIDATE_IDENTIFIED" })],
      strategic_recommendation_candidates: [],
    });
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Candidato de aprendizaje");
    expect(screen.queryByLabelText("Proponer una recomendación estratégica a partir de este aprendizaje validado")).not.toBeInTheDocument();
  });
});

describe("LearningPanel — recommendation decision (MVP-23B)", () => {
  it("OWNER/ADMIN sees Aceptar/Rechazar for an undecided recommendation and confirming applies the server response", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [makeCandidate({ id: "LRN-1", status: "VALIDATED" })],
      strategic_recommendation_candidates: [makeRecommendation({ id: "SRC-1", learning_candidate_id: "LRN-1" })],
    });
    mockDecideStrategicRecommendation.mockResolvedValue(makeRecommendation({ id: "SRC-1", learning_candidate_id: "LRN-1", decision: "ACCEPTED", decided_at: "2026-01-02T00:00:00Z" }));
    const user = userEvent.setup();
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    const acceptButton = await screen.findByRole("button", { name: "Aceptar" });

    await user.click(acceptButton);
    expect(mockDecideStrategicRecommendation).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Confirmar" }));
    expect(mockDecideStrategicRecommendation).toHaveBeenCalledWith("campaign-1", "SRC-1", "ACCEPTED");
    expect(await screen.findByText("Recomendación aceptada")).toBeInTheDocument();
  });
});

describe("LearningPanel — non-causal wording (MVP-23B §45)", () => {
  it("never uses causal, statistical, or Experiment/Hypothesis wording anywhere in the maturation UI", async () => {
    mockGetCampaignLearning.mockResolvedValue({
      learning_candidates: [
        makeCandidate({ id: "LRN-1", status: "VALIDATION_PENDING" }),
        makeCandidate({ id: "LRN-2", status: "VALIDATED" }),
      ],
      strategic_recommendation_candidates: [makeRecommendation({ id: "SRC-1", learning_candidate_id: "LRN-2" })],
    });
    render(<LearningPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Pendiente de validación");
    const text = document.body.textContent?.toLowerCase() ?? "";
    // Deliberately excludes "causal"/"significancia" as bare substrings —
    // the panel's own governance disclaimer legitimately contains them to
    // NEGATE a causal/statistical claim ("no implica una prueba causal ni
    // significancia estadística"), the same precedent
    // CampaignDistributionEvidenceRollupPanel's own non-causal subtitle
    // ("sin atribución causal") already established.
    for (const forbidden of [
      "causad", "caused", "generad", "atribuid", "mejor", "peor", "ganador", "rendimiento",
      "probado", "comprobado", "hipótesis", "experimento",
    ]) {
      expect(text).not.toContain(forbidden);
    }
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


function makeQualification(overrides: Partial<LearningQualificationPublic> = {}): LearningQualificationPublic {
  return { confidence: "LOW", replication_status: "REPLICATION_NOT_ESTABLISHED", consistency_status: "NOT_ASSESSED",
    scope: "This audience", generalization_boundary: "This period only", supporting_signal_ids: [], contradicting_signal_ids: [],
    evidence: [], validation_blockers: [], created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", ...overrides };
}

describe("MVP25 qualification governance", () => {
  it("shows legacy qualification-null with frozen fields and recommendation eligibility", async () => {
    mockGetCampaignLearning.mockResolvedValue({ learning_candidates: [makeCandidate({ status: "VALIDATED", qualification: null })], strategic_recommendation_candidates: [] });
    render(<LearningPanel campaignId="CMP-1" active refreshToken={0} />);
    expect(await screen.findByText(/Calificación no registrada — este aprendizaje se validó antes/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Guardar calificación" })).not.toBeInTheDocument();
    expect(screen.getByText(/Calificación e historial congelados/)).toBeInTheDocument();
  });

  it("shows server consistency, OTHER blocker and attachment-error history without hiding evidence", async () => {
    const row = { performance_signal_id: "SIG-OTHER", relationship: "CONTRADICTING" as const, note: "Observed contrary evidence", created_at: "2026-01-01T00:00:00Z", removed_at: "2026-01-02T00:00:00Z", removal_reason: "OTHER" as const, removal_note: "Retired from display", removed_by_user_id: "USR-1", governance_effective: true, blocks_validation: true };
    const qualification = makeQualification({ consistency_status: "MIXED", validation_blockers: ["CONTRADICTING_EVIDENCE"], evidence: [row, { ...row, performance_signal_id: "SIG-ERROR", removal_reason: "ATTACHMENT_ERROR", governance_effective: false, blocks_validation: false }, { ...row, performance_signal_id: "SIG-SUPPORT", relationship: "SUPPORTING", removed_at: null, removal_reason: null, removal_note: null, removed_by_user_id: null, blocks_validation: false }] });
    mockGetCampaignLearning.mockResolvedValue({ learning_candidates: [makeCandidate({ status: "VALIDATION_PENDING", qualification })], strategic_recommendation_candidates: [] });
    render(<LearningPanel campaignId="CMP-1" active refreshToken={0} />);
    expect(await screen.findByText("Consistencia: Mixta")).toBeInTheDocument();
    expect(screen.getByText(/Disposición OTHER: sigue contando/)).toBeInTheDocument();
    expect(screen.getByText(/Historial: error de vinculación/)).toBeInTheDocument();
    expect(screen.getByText("SIG-SUPPORT")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Validar aprendizaje" })).toBeDisabled();
    expect(screen.getByText(/Replicación: Valoración humana; no verificada/)).toBeInTheDocument();
  });

  it("edits confidence and reloads server truth with single-flight protection", async () => {
    const user = userEvent.setup();
    const candidate = makeCandidate({ status: "VALIDATION_PENDING", qualification: makeQualification() });
    mockGetCampaignLearning.mockResolvedValueOnce({ learning_candidates: [candidate], strategic_recommendation_candidates: [] });
    mockGetCampaignLearning.mockResolvedValueOnce({ learning_candidates: [{ ...candidate, qualification: makeQualification({ confidence: "MEDIUM", updated_at: "2026-02-01T00:00:00Z" }) }], strategic_recommendation_candidates: [] });
    let resolve!: (value: LearningCandidatePublic) => void;
    vi.mocked(updateLearningQualification).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
    render(<LearningPanel campaignId="CMP-1" active refreshToken={0} />);
    await screen.findByRole("button", { name: "Guardar calificación" });
    await user.selectOptions(screen.getByLabelText("Confianza"), "HIGH");
    await user.dblClick(screen.getByRole("button", { name: "Guardar calificación" }));
    expect(updateLearningQualification).toHaveBeenCalledTimes(1);
    expect(updateLearningQualification).toHaveBeenCalledWith("CMP-1", "LRN-1", expect.objectContaining({ confidence: "HIGH" }));
    resolve(candidate);
    expect(await screen.findByText("Confianza: Media")).toBeInTheDocument();
    expect(mockGetCampaignLearning).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText("Confianza")).toHaveValue("MEDIUM");
  });

  it("preserves server state after failure and permits retry", async () => {
    const user = userEvent.setup();
    mockGetCampaignLearning.mockResolvedValue({ learning_candidates: [makeCandidate({ status: "VALIDATION_PENDING", qualification: null })], strategic_recommendation_candidates: [] });
    vi.mocked(updateLearningQualification).mockRejectedValueOnce(new Error("Unavailable"));
    render(<LearningPanel campaignId="CMP-1" active refreshToken={0} />);
    await user.click(await screen.findByRole("button", { name: "Guardar calificación" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Guardar calificación" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "Validar aprendizaje" })).toBeDisabled();
    expect(mockGetCampaignLearning).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Calificación no registrada.")).toBeInTheDocument();
  });

  it("MEMBER edits evidence but cannot make final decisions", async () => {
    const user = userEvent.setup();
    mockUseAuth.mockReturnValue(makeAuth("MEMBER"));
    const candidate = makeCandidate({ status: "VALIDATION_PENDING", qualification: makeQualification() });
    mockGetCampaignLearning.mockResolvedValue({ learning_candidates: [candidate], strategic_recommendation_candidates: [] });
    vi.mocked(attachLearningEvidence).mockResolvedValue(candidate);
    render(<LearningPanel campaignId="CMP-1" active refreshToken={0} />);
    await screen.findByRole("button", { name: "Guardar calificación" });
    expect(screen.queryByRole("button", { name: "Validar aprendizaje" })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("ID de señal adicional"), "SIG-NEW");
    await user.selectOptions(screen.getByLabelText("Relación"), "CONTRADICTING");
    expect(screen.getByRole("button", { name: "Vincular evidencia adicional" })).toBeDisabled();
    await user.type(screen.getByLabelText("Justificación de la evidencia"), "Different result");
    await user.selectOptions(screen.getByLabelText("Declaración para esta vinculación o disposición"), "REPLICATION_FAILED");
    await user.click(screen.getByRole("button", { name: "Vincular evidencia adicional" }));
    expect(attachLearningEvidence).toHaveBeenCalledWith("CMP-1", "LRN-1", { performance_signal_id: "SIG-NEW", relationship: "CONTRADICTING", note: "Different result", replication_status: "REPLICATION_FAILED" });
    await waitFor(() => expect(mockGetCampaignLearning).toHaveBeenCalledTimes(2));
  });

  it("requires disposition rationale and sends explicit atomic replication choice", async () => {
    const user = userEvent.setup();
    const candidate = makeCandidate({ status: "VALIDATION_PENDING", qualification: makeQualification({ evidence: [{ performance_signal_id: "SIG-1", relationship: "CONTRADICTING", note: "Contrary result", created_at: "2026-01-01T00:00:00Z", removed_at: null, removal_reason: null, removal_note: null, removed_by_user_id: null, governance_effective: true, blocks_validation: true }], validation_blockers: ["CONTRADICTING_EVIDENCE"], consistency_status: "CONTRADICTING" }) });
    mockGetCampaignLearning.mockResolvedValue({ learning_candidates: [candidate], strategic_recommendation_candidates: [] });
    vi.mocked(disposeLearningEvidence).mockResolvedValue(candidate);
    render(<LearningPanel campaignId="CMP-1" active refreshToken={0} />);
    const button = await screen.findByRole("button", { name: "Registrar disposición de SIG-1" });
    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText("Justificación de la disposición"), "Wrong attachment");
    await user.selectOptions(screen.getByLabelText("Declaración para esta vinculación o disposición"), "REPLICATION_NOT_ESTABLISHED");
    await user.click(button);
    expect(disposeLearningEvidence).toHaveBeenCalledWith("CMP-1", "LRN-1", "SIG-1", { removal_reason: "ATTACHMENT_ERROR", removal_note: "Wrong attachment", replication_status: "REPLICATION_NOT_ESTABLISHED" });
  });
});
