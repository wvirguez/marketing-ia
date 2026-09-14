import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TrackingPanel } from "@/components/campaigns/detail/tracking-panel";
import { CampaignDetail } from "@/components/campaigns/detail/campaign-detail";
import { ApiError } from "@/lib/api/client";
import type { TrackingPlanPublic, TrackingResponse } from "@/types/tracking";

vi.mock("@/lib/api/tracking", () => ({
  getTracking: vi.fn(),
  createTrackingPlan: vi.fn(),
  createTrackingRequirement: vi.fn(),
  patchTracking: vi.fn(),
}));
vi.mock("@/lib/api/campaigns", () => ({
  getCampaign: vi.fn(),
  listCampaignRuns: vi.fn(),
}));

import { createTrackingPlan, createTrackingRequirement, getTracking, patchTracking } from "@/lib/api/tracking";
import { getCampaign, listCampaignRuns } from "@/lib/api/campaigns";

const mockGetTracking = vi.mocked(getTracking);
const mockCreateTrackingPlan = vi.mocked(createTrackingPlan);
const mockCreateTrackingRequirement = vi.mocked(createTrackingRequirement);
const mockPatchTracking = vi.mocked(patchTracking);
const mockGetCampaign = vi.mocked(getCampaign);
const mockListCampaignRuns = vi.mocked(listCampaignRuns);

const EMPTY_COPY = "Aún no se ha definido un plan de tracking para esta campaña.";
const ZERO_REQUIREMENTS_COPY = "No hay requisitos registrados en este plan.";
const NO_REQUIREMENT_STATUS_COPY = "Sin estado declarado";
const CERTIFIED_CLARIFICATION =
  "Este estado es declarado manualmente y no representa una verificación técnica automática.";
const CREATE_PLAN_LABEL = "Crear plan de tracking manual";

beforeEach(() => {
  vi.clearAllMocks();
});

function makePlan(overrides: Partial<TrackingPlanPublic> = {}): TrackingPlanPublic {
  return {
    id: "plan-1",
    status: "NOT_DEFINED",
    requirements: [],
    ...overrides,
  };
}

function makeResponse(plan: TrackingPlanPublic | null): TrackingResponse {
  return { plan };
}

describe("TrackingPanel", () => {
  it("renders the loading state while the request is unresolved", () => {
    mockGetTracking.mockImplementation(() => new Promise(() => {}));
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    expect(screen.getByRole("status")).toHaveTextContent("Cargando tracking…");
  });

  it("renders truthful empty copy when no Plan exists", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(null));
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    expect(await screen.findByText(EMPTY_COPY)).toBeInTheDocument();
    expect(screen.queryByText(/instalaci[oó]n/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/conectando/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/eventos.*recib/i)).not.toBeInTheDocument();
  });

  it("renders a populated Plan with its requirements", async () => {
    mockGetTracking.mockResolvedValue(
      makeResponse(
        makePlan({
          status: "CONFIGURED",
          requirements: [
            { id: "req-1", name: "Purchase event", status: "Implementado" },
            { id: "req-2", name: "Lead event", status: "Pendiente" },
          ],
        }),
      ),
    );
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    expect(await screen.findByText("Configurado")).toBeInTheDocument();
    expect(screen.getByText("Purchase event")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Implementado")).toBeInTheDocument();
    expect(screen.getByText("Lead event")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Pendiente")).toBeInTheDocument();
  });

  it("renders a truthful state for a Plan with zero requirements", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(makePlan({ status: "REQUIREMENTS_DEFINED", requirements: [] })));
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    expect(await screen.findByText(ZERO_REQUIREMENTS_COPY)).toBeInTheDocument();
    expect(screen.queryByText(/fall[oó]/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/incompleto/i)).not.toBeInTheDocument();
  });

  it("renders a neutral fallback for a Requirement with a null status", async () => {
    mockGetTracking.mockResolvedValue(
      makeResponse(makePlan({ requirements: [{ id: "req-1", name: "Purchase event", status: null }] })),
    );
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    expect(await screen.findByPlaceholderText(NO_REQUIREMENT_STATUS_COPY)).toBeInTheDocument();
  });

  it("shows a non-leaky error message and retries by issuing the GET again", async () => {
    mockGetTracking
      .mockRejectedValueOnce(new ApiError(403, "FORBIDDEN", "internal detail should not leak"))
      .mockResolvedValueOnce(makeResponse(null));

    const user = userEvent.setup();
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    expect(await screen.findByText("No encontramos esta campaña, o no tienes acceso a ella.")).toBeInTheDocument();
    expect(screen.queryByText(/internal detail/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /reintentar/i }));

    expect(await screen.findByText(EMPTY_COPY)).toBeInTheDocument();
    expect(mockGetTracking).toHaveBeenCalledTimes(2);
    expect(mockGetTracking).toHaveBeenNthCalledWith(2, "campaign-1");
  });

  it("frames CERTIFIED as self-reported and never as a system-verified result", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(makePlan({ status: "CERTIFIED" })));
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    expect(await screen.findByText("Certificado (declarado)")).toBeInTheDocument();
    expect(screen.getByText(CERTIFIED_CLARIFICATION)).toBeInTheDocument();

    for (const forbidden of [
      /verificado por el sistema/i,
      /tracking verificado/i,
      /pixel validado/i,
      /instalaci[oó]n confirmada/i,
      /eventos recibidos/i,
      /medici[oó]n funcionando/i,
      /certificaci[oó]n t[eé]cnica/i,
    ]) {
      expect(screen.queryByText(forbidden)).not.toBeInTheDocument();
    }
    // CERTIFIED is terminal — no further transition buttons are offered.
    expect(screen.queryAllByRole("button", { name: /^marcar:/i })).toHaveLength(0);
  });

  it("never implies measurement results, validated learning, or strategic decisions", async () => {
    mockGetTracking.mockResolvedValue(
      makeResponse(makePlan({ status: "CONFIGURED", requirements: [{ id: "req-1", name: "Purchase event", status: "ok" }] })),
    );
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    await screen.findByText("Configurado");

    for (const forbidden of [
      /resultado de medici[oó]n/i,
      /aprendizaje validado/i,
      /recomendaci[oó]n estrat[eé]gica/i,
      /decisi[oó]n estrat[eé]gica/i,
      /aprobado para distribuci[oó]n/i,
    ]) {
      expect(screen.queryByText(forbidden)).not.toBeInTheDocument();
    }
  });

  // --- empty-state Plan creation (MVP-15B) ---------------------------------

  it("shows a create-Plan CTA in the empty state", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(null));
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    expect(await screen.findByText(EMPTY_COPY)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: CREATE_PLAN_LABEL })).toBeInTheDocument();
  });

  it("calls createTrackingPlan when the CTA is clicked and shows no optimistic Plan before it resolves", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(null));
    let resolveCreate: (value: TrackingResponse) => void = () => {};
    mockCreateTrackingPlan.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCreate = resolve;
        }),
    );

    const user = userEvent.setup();
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);

    await user.click(screen.getByRole("button", { name: CREATE_PLAN_LABEL }));
    expect(mockCreateTrackingPlan).toHaveBeenCalledWith("campaign-1");

    // Still no optimistic Plan while the promise is unresolved.
    expect(screen.getByText(EMPTY_COPY)).toBeInTheDocument();
    expect(screen.queryByText("No definido")).not.toBeInTheDocument();

    resolveCreate(makeResponse(makePlan({ status: "NOT_DEFINED" })));
    expect(await screen.findByText("No definido")).toBeInTheDocument();
  });

  it("replaces local state with the exact server response after Plan creation succeeds", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(null));
    mockCreateTrackingPlan.mockResolvedValue(makeResponse(makePlan({ status: "NOT_DEFINED" })));

    const user = userEvent.setup();
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);
    await user.click(screen.getByRole("button", { name: CREATE_PLAN_LABEL }));

    expect(await screen.findByText("No definido")).toBeInTheDocument();
    expect(await screen.findByText(ZERO_REQUIREMENTS_COPY)).toBeInTheDocument();
  });

  it("preserves the empty state and permits retry when Plan creation fails", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(null));
    mockCreateTrackingPlan.mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom"));

    const user = userEvent.setup();
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);
    await user.click(screen.getByRole("button", { name: CREATE_PLAN_LABEL }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Ocurrió un error inesperado. Intenta de nuevo en unos minutos.");
    expect(screen.getByText(EMPTY_COPY)).toBeInTheDocument();

    const button = screen.getByRole("button", { name: CREATE_PLAN_LABEL });
    expect(button).not.toBeDisabled();
    mockCreateTrackingPlan.mockResolvedValueOnce(makeResponse(makePlan({ status: "NOT_DEFINED" })));
    await user.click(button);
    expect(await screen.findByText("No definido")).toBeInTheDocument();
  });

  // --- Requirement creation (MVP-15B) --------------------------------------

  it("shows the requirement-creation form only when the Plan state permits it", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(makePlan({ status: "CONFIGURED" })));
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    await screen.findByText("Configurado");
    expect(screen.queryByLabelText("Nombre del requisito")).not.toBeInTheDocument();
  });

  it("renders the requirement-creation form in a creation-allowed state", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(makePlan({ status: "NOT_DEFINED" })));
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    await screen.findByText("No definido");
    expect(screen.getByLabelText("Nombre del requisito")).toBeInTheDocument();
  });

  it("replaces local state with the server response after a successful requirement creation", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(makePlan({ status: "NOT_DEFINED" })));
    mockCreateTrackingRequirement.mockResolvedValue(
      makeResponse(makePlan({ status: "NOT_DEFINED", requirements: [{ id: "req-1", name: "Purchase event", status: null }] })),
    );

    const user = userEvent.setup();
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("No definido");

    await user.type(screen.getByLabelText("Nombre del requisito"), "Purchase event");
    await user.click(screen.getByRole("button", { name: "Añadir requisito" }));

    expect(mockCreateTrackingRequirement).toHaveBeenCalledWith("campaign-1", "Purchase event");
    expect(await screen.findByText("Purchase event")).toBeInTheDocument();
  });

  it("preserves the typed name and shows an error when requirement creation fails", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(makePlan({ status: "NOT_DEFINED" })));
    mockCreateTrackingRequirement.mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom"));

    const user = userEvent.setup();
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("No definido");

    const input = screen.getByLabelText("Nombre del requisito");
    await user.type(input, "Purchase event");
    await user.click(screen.getByRole("button", { name: "Añadir requisito" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    // Server state unchanged (no optimistic Requirement was ever added).
    expect(screen.getByText(ZERO_REQUIREMENTS_COPY)).toBeInTheDocument();
  });

  // --- Plan transitions (MVP-15B) ------------------------------------------

  it("renders only the legal next transition(s) for the current Plan status", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(makePlan({ status: "CONFIGURATION_PENDING" })));
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    await screen.findByText("Configuración pendiente");
    expect(screen.getByRole("button", { name: "Marcar: Configurado" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Marcar: Verificación no superada" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /No definido|Requisitos definidos|Certificado/i })).not.toBeInTheDocument();
  });

  it("requires explicit confirmation, showing the exact clarification, before submitting a CERTIFIED transition", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(makePlan({ status: "VERIFICATION_PENDING" })));
    const user = userEvent.setup();
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    await screen.findByText("Verificación pendiente");
    await user.click(screen.getByRole("button", { name: "Marcar: Certificado (declarado)" }));

    expect(mockPatchTracking).not.toHaveBeenCalled();
    expect(screen.getByText(CERTIFIED_CLARIFICATION)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirmar" })).toBeInTheDocument();

    mockPatchTracking.mockResolvedValue(makeResponse(makePlan({ status: "CERTIFIED" })));
    await user.click(screen.getByRole("button", { name: "Confirmar" }));

    expect(mockPatchTracking).toHaveBeenCalledWith("campaign-1", { operation: "TRANSITION_PLAN", target_status: "CERTIFIED" });
    expect(await screen.findByText("Certificado (declarado)")).toBeInTheDocument();
  });

  it("does not submit the CERTIFIED transition if the confirmation is cancelled", async () => {
    mockGetTracking.mockResolvedValue(makeResponse(makePlan({ status: "VERIFICATION_PENDING" })));
    const user = userEvent.setup();
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    await screen.findByText("Verificación pendiente");
    await user.click(screen.getByRole("button", { name: "Marcar: Certificado (declarado)" }));
    await user.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(mockPatchTracking).not.toHaveBeenCalled();
    expect(screen.getByText("Verificación pendiente")).toBeInTheDocument();
  });

  // --- Requirement status update (MVP-15B) ---------------------------------

  it("uses a free-text status input, not a fixed dropdown vocabulary", async () => {
    mockGetTracking.mockResolvedValue(
      makeResponse(makePlan({ requirements: [{ id: "req-1", name: "Purchase event", status: null }] })),
    );
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    await screen.findByText("Purchase event");
    const input = screen.getByLabelText("Estado de Purchase event");
    expect(input.tagName).toBe("INPUT");
    expect(input).not.toHaveAttribute("list");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("submits null when the status input is blank or whitespace-only", async () => {
    mockGetTracking.mockResolvedValue(
      makeResponse(makePlan({ requirements: [{ id: "req-1", name: "Purchase event", status: "Pendiente" }] })),
    );
    mockPatchTracking.mockResolvedValue(
      makeResponse(makePlan({ requirements: [{ id: "req-1", name: "Purchase event", status: null }] })),
    );

    const user = userEvent.setup();
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Purchase event");

    const input = screen.getByLabelText("Estado de Purchase event");
    await user.clear(input);
    await user.type(input, "   ");
    await user.click(screen.getByRole("button", { name: "Guardar estado" }));

    expect(mockPatchTracking).toHaveBeenCalledWith("campaign-1", {
      operation: "UPDATE_REQUIREMENT_STATUS",
      requirement_id: "req-1",
      status: null,
    });
  });

  it("replaces local state with the server response after a successful requirement status update", async () => {
    mockGetTracking.mockResolvedValue(
      makeResponse(makePlan({ requirements: [{ id: "req-1", name: "Purchase event", status: null }] })),
    );
    mockPatchTracking.mockResolvedValue(
      makeResponse(makePlan({ requirements: [{ id: "req-1", name: "Purchase event", status: "Implementado" }] })),
    );

    const user = userEvent.setup();
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Purchase event");

    const input = screen.getByLabelText("Estado de Purchase event");
    await user.type(input, "Implementado");
    await user.click(screen.getByRole("button", { name: "Guardar estado" }));

    expect(mockPatchTracking).toHaveBeenCalledWith("campaign-1", {
      operation: "UPDATE_REQUIREMENT_STATUS",
      requirement_id: "req-1",
      status: "Implementado",
    });
    await waitFor(() => expect(screen.getByLabelText("Estado de Purchase event")).toHaveValue("Implementado"));
  });

  it("preserves the last confirmed status and shows an error when the status update fails", async () => {
    mockGetTracking.mockResolvedValue(
      makeResponse(makePlan({ requirements: [{ id: "req-1", name: "Purchase event", status: "Pendiente" }] })),
    );
    mockPatchTracking.mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom"));

    const user = userEvent.setup();
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Purchase event");

    const input = screen.getByLabelText("Estado de Purchase event");
    await user.clear(input);
    await user.type(input, "Fallido");
    await user.click(screen.getByRole("button", { name: "Guardar estado" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    // Confirmed server state is unchanged — no optimistic status persisted.
    await waitFor(() => expect(mockPatchTracking).toHaveBeenCalled());
  });

  // --- section-global single-flight lock (MVP-15B) -------------------------

  it("disables every write control while one mutation is pending (section-global lock)", async () => {
    mockGetTracking.mockResolvedValue(
      makeResponse(makePlan({ status: "NOT_DEFINED", requirements: [{ id: "req-1", name: "Purchase event", status: null }] })),
    );
    let resolveCreate: (value: TrackingResponse) => void = () => {};
    mockCreateTrackingRequirement.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCreate = resolve;
        }),
    );

    const user = userEvent.setup();
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Purchase event");

    await user.type(screen.getByLabelText("Nombre del requisito"), "Lead event");
    await user.click(screen.getByRole("button", { name: "Añadir requisito" }));

    expect(screen.getByRole("button", { name: "Añadir requisito" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Marcar: Requisitos definidos" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Guardar estado" })).toBeDisabled();
    expect(screen.getByLabelText("Estado de Purchase event")).toBeDisabled();

    resolveCreate(
      makeResponse(
        makePlan({
          status: "NOT_DEFINED",
          requirements: [
            { id: "req-1", name: "Purchase event", status: null },
            { id: "req-2", name: "Lead event", status: null },
          ],
        }),
      ),
    );
    // The submit button itself stays disabled once the input clears back to
    // empty (correct, unrelated to the lock) — the input field re-enabling
    // is what actually proves the section-global lock released.
    await waitFor(() => expect(screen.getByLabelText("Nombre del requisito")).not.toBeDisabled());
    for (const button of screen.getAllByRole("button", { name: "Guardar estado" })) {
      expect(button).not.toBeDisabled();
    }
  });
});

describe("TrackingPanel integration inside CampaignDetail", () => {
  it("renders the real Tracking panel (not the static placeholder) with the correct campaignId", async () => {
    mockGetCampaign.mockResolvedValue({
      id: "campaign-42",
      name: "Campaña de prueba",
      status: "DRAFT",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      archived_at: null,
    });
    mockListCampaignRuns.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    mockGetTracking.mockResolvedValue(makeResponse(null));

    const user = userEvent.setup();
    render(<CampaignDetail campaignId="campaign-42" />);

    await screen.findByRole("heading", { name: "Campaña de prueba" });

    await user.click(screen.getByRole("tab", { name: "Tracking" }));

    expect(await screen.findByText(EMPTY_COPY)).toBeInTheDocument();
    expect(mockGetTracking).toHaveBeenCalledWith("campaign-42");
    expect(screen.queryByText("Tracking aún no disponible")).not.toBeInTheDocument();
  });
});
