import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CommercialPanel } from "@/components/campaigns/detail/commercial-panel";
import { ApiError } from "@/lib/api/client";
import type { CommercialObjectivePublic, OfferPublic } from "@/types/commercial";

vi.mock("@/lib/api/commercial", () => ({
  getCommercialObjectives: vi.fn(),
  getOffers: vi.fn(),
  createCommercialObjective: vi.fn(),
  supersedeCommercialObjective: vi.fn(),
  createOffer: vi.fn(),
  supersedeOffer: vi.fn(),
}));

import {
  createCommercialObjective,
  createOffer,
  getCommercialObjectives,
  getOffers,
  supersedeCommercialObjective,
  supersedeOffer,
} from "@/lib/api/commercial";

const mockGetObjectives = vi.mocked(getCommercialObjectives);
const mockGetOffers = vi.mocked(getOffers);
const mockCreateObjective = vi.mocked(createCommercialObjective);
const mockSupersedeObjective = vi.mocked(supersedeCommercialObjective);
const mockCreateOffer = vi.mocked(createOffer);
const mockSupersedeOffer = vi.mocked(supersedeOffer);

function objective(overrides: Partial<CommercialObjectivePublic> = {}): CommercialObjectivePublic {
  return {
    id: "OBJ-1",
    campaign_id: "campaign-1",
    statement: "Generate qualified leads.",
    created_at: "2026-01-01T00:00:00Z",
    current: true,
    superseded_at: null,
    superseded_by_commercial_objective_id: null,
    ...overrides,
  };
}

function offer(overrides: Partial<OfferPublic> = {}): OfferPublic {
  return {
    id: "OFR-1",
    campaign_id: "campaign-1",
    statement: "Six-week course.",
    price: "199.00",
    currency: "USD",
    created_at: "2026-01-01T00:00:00Z",
    current: true,
    superseded_at: null,
    superseded_by_offer_id: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("CommercialPanel — fetch / render", () => {
  it("does not fetch while inactive", () => {
    render(<CommercialPanel campaignId="campaign-1" active={false} refreshToken={0} />);
    expect(mockGetObjectives).not.toHaveBeenCalled();
    expect(mockGetOffers).not.toHaveBeenCalled();
  });

  it("loads both GET /commercial-objectives and GET /offers once activated", async () => {
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(mockGetObjectives).toHaveBeenCalledWith("campaign-1"));
    expect(mockGetOffers).toHaveBeenCalledWith("campaign-1");
  });

  it("renders the loading state", () => {
    mockGetObjectives.mockImplementation(() => new Promise(() => {}));
    mockGetOffers.mockImplementation(() => new Promise(() => {}));
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(screen.getByText("Cargando definición comercial…")).toBeInTheDocument();
  });

  it("shows the neutral empty state for both sections with no entities", async () => {
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Aún no se ha definido un objetivo comercial para esta campaña.")).toBeInTheDocument());
    expect(screen.getByText("Aún no se ha definido una oferta para esta campaña.")).toBeInTheDocument();
  });

  it("renders a current objective's statement and a current offer's price", async () => {
    mockGetObjectives.mockResolvedValue([objective()]);
    mockGetOffers.mockResolvedValue([offer()]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Generate qualified leads.")).toBeInTheDocument());
    expect(screen.getByText("199.00 USD")).toBeInTheDocument();
  });

  it("renders a free offer distinctly from an unknown-price offer", async () => {
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([
      offer({ id: "OFR-FREE", price: "0", currency: "USD" }),
      offer({ id: "OFR-UNKNOWN", price: null, currency: null }),
    ]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Gratis (USD)")).toBeInTheDocument());
    expect(screen.getByText("Precio no definido")).toBeInTheDocument();
  });

  it("separates current objectives from historical (superseded) ones under a collapsed history", async () => {
    mockGetObjectives.mockResolvedValue([
      objective({ id: "OBJ-OLD", statement: "Old objective.", current: false, superseded_at: "2026-01-02T00:00:00Z", superseded_by_commercial_objective_id: "OBJ-NEW" }),
      objective({ id: "OBJ-NEW", statement: "New objective." }),
    ]);
    mockGetOffers.mockResolvedValue([]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("New objective.")).toBeInTheDocument());
    // The historical one is not shown as a current card, but is present under the collapsed history.
    expect(screen.getByText("Historial de objetivos (1)")).toBeInTheDocument();
  });
});

describe("CommercialPanel — independent create vs. supersede are distinct actions", () => {
  it("clicking 'Agregar objetivo' calls createCommercialObjective, never supersede", async () => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockCreateObjective.mockResolvedValue(objective());
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByLabelText("Agregar otro objetivo")).toBeInTheDocument());
    await user.type(screen.getByLabelText("Agregar otro objetivo"), "Generate revenue.");
    await user.click(screen.getByRole("button", { name: "Agregar objetivo" }));
    await waitFor(() => expect(mockCreateObjective).toHaveBeenCalledWith("campaign-1", "Generate revenue."));
    expect(mockSupersedeObjective).not.toHaveBeenCalled();
  });

  it("clicking 'Reemplazar este objetivo' then confirming calls supersedeCommercialObjective, never create", async () => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([objective()]);
    mockGetOffers.mockResolvedValue([]);
    mockSupersedeObjective.mockResolvedValue(objective({ id: "OBJ-2", statement: "Generate revenue instead." }));
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Generate qualified leads.")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Reemplazar este objetivo" }));
    await user.type(screen.getByLabelText("Nuevo enunciado"), "Generate revenue instead.");
    await user.click(screen.getByRole("button", { name: "Confirmar reemplazo" }));
    await waitFor(() =>
      expect(mockSupersedeObjective).toHaveBeenCalledWith("campaign-1", "OBJ-1", "Generate revenue instead."),
    );
    expect(mockCreateObjective).not.toHaveBeenCalled();
  });

  it("clicking 'Agregar oferta' calls createOffer with null price/currency when both are left empty", async () => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockCreateOffer.mockResolvedValue(offer());
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByLabelText("Agregar otra oferta")).toBeInTheDocument());
    await user.type(screen.getByLabelText("Agregar otra oferta"), "New offer.");
    await user.click(screen.getByRole("button", { name: "Agregar oferta" }));
    await waitFor(() => expect(mockCreateOffer).toHaveBeenCalledWith("campaign-1", "New offer.", null, null));
  });

  it("clicking 'Reemplazar esta oferta' then confirming calls supersedeOffer, never create", async () => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([offer()]);
    mockSupersedeOffer.mockResolvedValue(offer({ id: "OFR-2", statement: "Course, new price.", price: "249.00" }));
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Six-week course.")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Reemplazar esta oferta" }));
    await user.type(screen.getByLabelText("Nuevo enunciado"), "Course, new price.");
    await user.click(screen.getByRole("button", { name: "Confirmar reemplazo" }));
    await waitFor(() =>
      expect(mockSupersedeOffer).toHaveBeenCalledWith("campaign-1", "OFR-1", "Course, new price.", null, null),
    );
    expect(mockCreateOffer).not.toHaveBeenCalled();
  });

  it("disables 'Agregar oferta' when only price is filled in (pair invariant enforced client-side)", async () => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByLabelText("Agregar otra oferta")).toBeInTheDocument());
    await user.type(screen.getByLabelText("Agregar otra oferta"), "New offer.");
    await user.type(screen.getByLabelText("Precio (opcional)"), "10.00");
    expect(screen.getByRole("button", { name: "Agregar oferta" })).toBeDisabled();
  });
});

describe("CommercialPanel — error handling", () => {
  it("shows a retry option on a failed load", async () => {
    mockGetObjectives.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
    mockGetOffers.mockResolvedValue([]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Reintentar" })).toBeInTheDocument());
  });

  it("shows a mutation error and leaves the last confirmed state on a failed supersede", async () => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([objective()]);
    mockGetOffers.mockResolvedValue([]);
    mockSupersedeObjective.mockRejectedValue(
      new ApiError(409, "COMMERCIAL_OBJECTIVE_ALREADY_SUPERSEDED", "This Commercial Objective has already been superseded."),
    );
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Generate qualified leads.")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Reemplazar este objetivo" }));
    await user.type(screen.getByLabelText("Nuevo enunciado"), "Second attempt.");
    await user.click(screen.getByRole("button", { name: "Confirmar reemplazo" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText("Generate qualified leads.")).toBeInTheDocument();
  });
});
