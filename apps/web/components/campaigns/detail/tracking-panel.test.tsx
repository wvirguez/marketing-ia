import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TrackingPanel } from "@/components/campaigns/detail/tracking-panel";
import { CampaignDetail } from "@/components/campaigns/detail/campaign-detail";
import { ApiError } from "@/lib/api/client";
import type { TrackingPlanPublic, TrackingResponse } from "@/types/tracking";

vi.mock("@/lib/api/tracking", () => ({
  getTracking: vi.fn(),
}));
vi.mock("@/lib/api/campaigns", () => ({
  getCampaign: vi.fn(),
  listCampaignRuns: vi.fn(),
}));

import { getTracking } from "@/lib/api/tracking";
import { getCampaign, listCampaignRuns } from "@/lib/api/campaigns";

const mockGetTracking = vi.mocked(getTracking);
const mockGetCampaign = vi.mocked(getCampaign);
const mockListCampaignRuns = vi.mocked(listCampaignRuns);

const EMPTY_COPY = "Aún no se ha definido un plan de tracking para esta campaña.";
const ZERO_REQUIREMENTS_COPY = "No hay requisitos registrados en este plan.";
const NO_REQUIREMENT_STATUS_COPY = "Sin estado declarado";
const CERTIFIED_CLARIFICATION =
  "Este estado es declarado manualmente y no representa una verificación técnica automática.";

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
    expect(screen.getByText("Implementado")).toBeInTheDocument();
    expect(screen.getByText("Lead event")).toBeInTheDocument();
    expect(screen.getByText("Pendiente")).toBeInTheDocument();
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

    expect(await screen.findByText(NO_REQUIREMENT_STATUS_COPY)).toBeInTheDocument();
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
    const { container } = render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

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
    // No colored approval/success pill is reused for a self-reported status.
    expect(container.querySelector(".status")).toBeNull();
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

  it("exposes no write control — no transition button, no certify action", async () => {
    mockGetTracking.mockResolvedValue(
      makeResponse(makePlan({ status: "VERIFICATION_PENDING", requirements: [{ id: "req-1", name: "Purchase event", status: null }] })),
    );
    render(<TrackingPanel campaignId="campaign-1" active refreshToken={0} />);

    await screen.findByText("Verificación pendiente");

    const buttons = screen.queryAllByRole("button");
    expect(buttons).toHaveLength(0);
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
