import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CommercialPanel } from "@/components/campaigns/detail/commercial-panel";
import { ApiError } from "@/lib/api/client";
import type { CommercialObjectivePublic, CommercialOutcomePublic, OfferPublic } from "@/types/commercial";

vi.mock("@/lib/api/commercial", () => ({
  getCommercialObjectives: vi.fn(),
  getOffers: vi.fn(),
  getCommercialOutcomes: vi.fn(),
  createCommercialObjective: vi.fn(),
  supersedeCommercialObjective: vi.fn(),
  createOffer: vi.fn(),
  supersedeOffer: vi.fn(),
  createCommercialOutcome: vi.fn(),
  correctCommercialOutcome: vi.fn(),
}));

import {
  correctCommercialOutcome,
  createCommercialObjective,
  createCommercialOutcome,
  createOffer,
  getCommercialObjectives,
  getCommercialOutcomes,
  getOffers,
  supersedeCommercialObjective,
  supersedeOffer,
} from "@/lib/api/commercial";

const mockGetObjectives = vi.mocked(getCommercialObjectives);
const mockGetOffers = vi.mocked(getOffers);
const mockGetOutcomes = vi.mocked(getCommercialOutcomes);
const mockCreateObjective = vi.mocked(createCommercialObjective);
const mockSupersedeObjective = vi.mocked(supersedeCommercialObjective);
const mockCreateOffer = vi.mocked(createOffer);
const mockSupersedeOffer = vi.mocked(supersedeOffer);
const mockCreateOutcome = vi.mocked(createCommercialOutcome);
const mockCorrectOutcome = vi.mocked(correctCommercialOutcome);

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

function outcome(overrides: Partial<CommercialOutcomePublic> = {}): CommercialOutcomePublic {
  return {
    id: "OUT-1",
    campaign_id: "campaign-1",
    content_distribution_id: null,
    outcome_type: "lead",
    quantity: null,
    monetary_value: null,
    currency: null,
    occurred_at: "2026-01-15T12:00:00Z",
    created_at: "2026-01-15T12:05:00Z",
    external_reference: null,
    is_current: true,
    supersedes_outcome_id: null,
    corrected_by_commercial_outcome_id: null,
    correction_reason: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // Every existing objective/offer-focused test leaves outcomes
  // unspecified — default to an empty list so Promise.all never resolves
  // to `undefined` for a fixture that doesn't care about this section.
  mockGetOutcomes.mockResolvedValue([]);
});

describe("CommercialPanel — fetch / render", () => {
  it("does not fetch while inactive", () => {
    render(<CommercialPanel campaignId="campaign-1" active={false} refreshToken={0} />);
    expect(mockGetObjectives).not.toHaveBeenCalled();
    expect(mockGetOffers).not.toHaveBeenCalled();
    expect(mockGetOutcomes).not.toHaveBeenCalled();
  });

  it("loads GET /commercial-objectives, /offers, and /commercial-outcomes once activated", async () => {
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(mockGetObjectives).toHaveBeenCalledWith("campaign-1"));
    expect(mockGetOffers).toHaveBeenCalledWith("campaign-1");
    expect(mockGetOutcomes).toHaveBeenCalledWith("campaign-1");
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

describe("CommercialPanel — Commercial Outcome (MVP-36A/-R1/-36B)", () => {
  it("shows the neutral empty state when no outcomes exist", async () => {
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockGetOutcomes.mockResolvedValue([]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() =>
      expect(screen.getByText("Aún no se ha registrado un resultado comercial para esta campaña.")).toBeInTheDocument(),
    );
  });

  it("renders a current outcome's type, quantity, money, and Distribution association", async () => {
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockGetOutcomes.mockResolvedValue([
      outcome({ outcome_type: "purchase", quantity: 3, monetary_value: "97.50", currency: "USD", content_distribution_id: "DST-1" }),
    ]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("purchase")).toBeInTheDocument());
    expect(screen.getByText("Cantidad: 3")).toBeInTheDocument();
    expect(screen.getByText("Valor: 97.50 USD")).toBeInTheDocument();
    expect(screen.getByText(/asociado observacionalmente con la distribución DST-1/i)).toBeInTheDocument();
  });

  it("clicking 'Registrar resultado comercial' then submitting calls createCommercialOutcome with a fresh client_request_id, never correction", async () => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockGetOutcomes.mockResolvedValue([]);
    mockCreateOutcome.mockResolvedValue(outcome());
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Registrar resultado comercial" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Registrar resultado comercial" }));
    await user.type(screen.getByLabelText("Tipo de resultado"), "lead");
    await user.type(screen.getByLabelText("Fecha en que ocurrió"), "2026-01-15T12:00");
    await user.click(screen.getByRole("button", { name: "Registrar resultado" }));
    await waitFor(() => expect(mockCreateOutcome).toHaveBeenCalledTimes(1));
    const [campaignId, body] = mockCreateOutcome.mock.calls[0];
    expect(campaignId).toBe("campaign-1");
    expect(body.outcome_type).toBe("lead");
    expect(typeof body.client_request_id).toBe("string");
    expect(body.client_request_id.length).toBeGreaterThan(0);
    expect(mockCorrectOutcome).not.toHaveBeenCalled();
  });

  it("disables submit when only monetary_value is filled in (pair invariant enforced client-side)", async () => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockGetOutcomes.mockResolvedValue([]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await user.click(await screen.findByRole("button", { name: "Registrar resultado comercial" }));
    await user.type(screen.getByLabelText("Tipo de resultado"), "lead");
    await user.type(screen.getByLabelText("Fecha en que ocurrió"), "2026-01-15T12:00");
    await user.type(screen.getByLabelText("Valor (opcional)"), "10.00");
    await user.click(screen.getByRole("button", { name: "Registrar resultado" }));
    expect(mockCreateOutcome).not.toHaveBeenCalled();
    expect(screen.getByText("Valor y moneda deben indicarse juntos, o ambos vacíos.")).toBeInTheDocument();
  });

  // The Offer forms carry an identically-labelled currency field, so scope to the Outcome form.
  const outcomeCurrencyInput = () =>
    within(screen.getByRole("heading", { name: "Registrar resultado comercial" }).closest("form")!).getByLabelText(
      "Moneda (ej. USD)",
    );

  // MVP-36B-R1: UX-only mirror of the backend numeric contract — the values
  // below are what the backend would 422, so the form never submits them.
  it.each([
    ["quantity above int4", "Cantidad (opcional)", "2147483648", /entre 1 y 2147483647/],
    ["quantity zero", "Cantidad (opcional)", "0", /entre 1 y 2147483647/],
    ["money with more than 4 decimals", "Valor (opcional)", "1.23456", /máximo 4 decimales/],
    ["money above the Numeric(12,4) maximum", "Valor (opcional)", "100000000", /entre 0 y 99999999.9999/],
    ["negative money", "Valor (opcional)", "-1", /entre 0 y 99999999.9999/],
  ])("does not submit out-of-contract input: %s", async (_name, label, value, message) => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockGetOutcomes.mockResolvedValue([]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await user.click(await screen.findByRole("button", { name: "Registrar resultado comercial" }));
    await user.type(screen.getByLabelText("Tipo de resultado"), "lead");
    await user.type(screen.getByLabelText("Fecha en que ocurrió"), "2026-01-15T12:00");
    await user.type(screen.getByLabelText(label), value);
    if (label === "Valor (opcional)") await user.type(outcomeCurrencyInput(), "USD");
    await user.click(screen.getByRole("button", { name: "Registrar resultado" }));
    expect(mockCreateOutcome).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(message);
  });

  it("submits the maximum in-contract quantity and money, with an aware ISO occurred_at", async () => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockGetOutcomes.mockResolvedValue([]);
    mockCreateOutcome.mockResolvedValue(outcome());
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await user.click(await screen.findByRole("button", { name: "Registrar resultado comercial" }));
    await user.type(screen.getByLabelText("Tipo de resultado"), "purchase");
    await user.type(screen.getByLabelText("Fecha en que ocurrió"), "2026-01-15T12:00");
    await user.type(screen.getByLabelText("Cantidad (opcional)"), "2147483647");
    await user.type(screen.getByLabelText("Valor (opcional)"), "99999999.9999");
    await user.type(outcomeCurrencyInput(), "USD");
    await user.click(screen.getByRole("button", { name: "Registrar resultado" }));
    await waitFor(() => expect(mockCreateOutcome).toHaveBeenCalledTimes(1));
    const body = mockCreateOutcome.mock.calls[0][1];
    expect(body.quantity).toBe(2147483647);
    expect(body.monetary_value).toBe("99999999.9999");
    // toISOString() always carries an explicit UTC designator — never naive.
    expect(body.occurred_at).toMatch(/Z$/);
  });

  // MVP-36B-R2/R3: UX-only mirror of the backend's safe occurred_at range
  // (eight calendar days inside year 1..9999 at each end). The chosen values sit
  // far enough from the boundaries to hold under any machine timezone (offsets
  // are always < 24h).
  it.each([
    ["year 0001", "0001-01-01T00:00"],
    ["the R2 lower boundary, now outside the range", "0001-01-02T00:00"],
    ["the R2 upper boundary, now outside the range", "9999-12-30T00:00"],
    ["last day of year 9999", "9999-12-31T23:59"],
  ])("does not submit an occurred_at outside the backend's safe range: %s", async (_name, value) => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockGetOutcomes.mockResolvedValue([]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await user.click(await screen.findByRole("button", { name: "Registrar resultado comercial" }));
    await user.type(screen.getByLabelText("Tipo de resultado"), "lead");
    await user.type(screen.getByLabelText("Fecha en que ocurrió"), value);
    await user.click(screen.getByRole("button", { name: "Registrar resultado" }));
    expect(mockCreateOutcome).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/entre el 9 de enero de 0001 y el 23 de diciembre de 9999/);
  });

  it.each([
    ["early in year 0001", "0001-01-10T00:00"],
    ["late in year 9999", "9999-12-22T23:59"],
    ["an ordinary date", "2026-03-01T10:00"],
  ])("still submits an in-range occurred_at as an aware ISO instant: %s", async (_name, value) => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockGetOutcomes.mockResolvedValue([]);
    mockCreateOutcome.mockResolvedValue(outcome());
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await user.click(await screen.findByRole("button", { name: "Registrar resultado comercial" }));
    await user.type(screen.getByLabelText("Tipo de resultado"), "lead");
    await user.type(screen.getByLabelText("Fecha en que ocurrió"), value);
    await user.click(screen.getByRole("button", { name: "Registrar resultado" }));
    await waitFor(() => expect(mockCreateOutcome).toHaveBeenCalledTimes(1));
    const body = mockCreateOutcome.mock.calls[0][1];
    expect(body.occurred_at).toBe(new Date(value).toISOString());
    expect(body.occurred_at).toMatch(/Z$/);
  });

  it("clicking 'Corregir' then submitting calls correctCommercialOutcome with a full-state payload, never create, and never offers a content_distribution_id field", async () => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockGetOutcomes.mockResolvedValue([outcome({ id: "OUT-1", outcome_type: "lead", content_distribution_id: "DST-1" })]);
    mockCorrectOutcome.mockResolvedValue(outcome({ id: "OUT-2", outcome_type: "purchase", supersedes_outcome_id: "OUT-1" }));
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("lead")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Corregir" }));

    // The correction form never exposes a Distribution field at all.
    expect(screen.queryByLabelText(/distribución asociada/i)).not.toBeInTheDocument();
    expect(screen.getByText(/no editable en una corrección/i)).toBeInTheDocument();

    const typeInput = screen.getByLabelText("Tipo de resultado");
    await user.clear(typeInput);
    await user.type(typeInput, "purchase");
    await user.type(screen.getByLabelText("Motivo de la corrección"), "Se confirmó la compra.");
    await user.click(screen.getByRole("button", { name: "Guardar corrección" }));

    await waitFor(() => expect(mockCorrectOutcome).toHaveBeenCalledTimes(1));
    const [campaignId, outcomeId, body] = mockCorrectOutcome.mock.calls[0];
    expect(campaignId).toBe("campaign-1");
    expect(outcomeId).toBe("OUT-1");
    expect(body.outcome_type).toBe("purchase");
    expect(body.correction_reason).toBe("Se confirmó la compra.");
    expect(body).not.toHaveProperty("content_distribution_id");
    expect(mockCreateOutcome).not.toHaveBeenCalled();
  });

  it("requires a correction reason before submitting a correction", async () => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockGetOutcomes.mockResolvedValue([outcome()]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await user.click(await screen.findByRole("button", { name: "Corregir" }));
    await user.click(screen.getByRole("button", { name: "Guardar corrección" }));
    expect(mockCorrectOutcome).not.toHaveBeenCalled();
    expect(screen.getByText("Explica por qué corriges este registro.")).toBeInTheDocument();
  });

  it("separates current outcomes from historical (corrected) ones under a collapsed history, and never offers 'Corregir' on a historical row", async () => {
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockGetOutcomes.mockResolvedValue([
      outcome({ id: "OUT-1", outcome_type: "lead", is_current: false, corrected_by_commercial_outcome_id: "OUT-2", correction_reason: null }),
      outcome({ id: "OUT-2", outcome_type: "qualified lead", supersedes_outcome_id: "OUT-1", correction_reason: "Se calificó el lead." }),
    ]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("qualified lead")).toBeInTheDocument());
    expect(screen.getByText("Historial de resultados comerciales (1)")).toBeInTheDocument();
    // The historical row's own "Corregir" affordance must not be offered
    // anywhere — only the current row's card shows one.
    expect(screen.getAllByRole("button", { name: "Corregir" })).toHaveLength(1);
  });

  it("never renders causal/attribution wording anywhere in this section", async () => {
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockGetOutcomes.mockResolvedValue([outcome({ content_distribution_id: "DST-1" })]);
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("lead")).toBeInTheDocument());
    expect(screen.queryByText(/generado por/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/causado por/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/conversión de este contenido/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/roi de esta pieza/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/ganador/i)).not.toBeInTheDocument();
  });

  it("shows a mutation error and leaves the last confirmed outcome state on a failed create", async () => {
    const user = userEvent.setup();
    mockGetObjectives.mockResolvedValue([]);
    mockGetOffers.mockResolvedValue([]);
    mockGetOutcomes.mockResolvedValue([outcome()]);
    mockCreateOutcome.mockRejectedValue(new ApiError(409, "IDEMPOTENCY_KEY_CONFLICT", "client_request_id has already been used for another operation."));
    render(<CommercialPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("lead")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Registrar resultado comercial" }));
    await user.type(screen.getByLabelText("Tipo de resultado"), "purchase");
    await user.type(screen.getByLabelText("Fecha en que ocurrió"), "2026-01-15T12:00");
    await user.click(screen.getByRole("button", { name: "Registrar resultado" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText("lead")).toBeInTheDocument(); // last confirmed list untouched
  });
});
