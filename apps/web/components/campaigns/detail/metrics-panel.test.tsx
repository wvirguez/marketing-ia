import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MetricsPanel } from "@/components/campaigns/detail/metrics-panel";
import { CampaignDetail } from "@/components/campaigns/detail/campaign-detail";
import { ApiError } from "@/lib/api/client";
import type { MetricEntryPublic, MetricEntryWriteRequest } from "@/types/measurement";

vi.mock("@/lib/api/measurement", () => ({
  getMetrics: vi.fn(),
  createMetricEntry: vi.fn(),
}));
vi.mock("@/lib/api/campaigns", () => ({
  getCampaign: vi.fn(),
  listCampaignRuns: vi.fn(),
}));

import { createMetricEntry, getMetrics } from "@/lib/api/measurement";
import { getCampaign, listCampaignRuns } from "@/lib/api/campaigns";

const mockGetMetrics = vi.mocked(getMetrics);
const mockCreateMetricEntry = vi.mocked(createMetricEntry);
const mockGetCampaign = vi.mocked(getCampaign);
const mockListCampaignRuns = vi.mocked(listCampaignRuns);

const EMPTY_COPY = "Aún no hay métricas registradas para esta campaña.";
const SUCCESS_COPY = "Métricas registradas.";
const CURRENT_COPY = "Registro actual para este período y canal";
const PRIOR_COPY = "Registro anterior";

let uuidCounter = 0;

beforeEach(() => {
  vi.clearAllMocks();
  uuidCounter = 0;
  vi.spyOn(crypto, "randomUUID").mockImplementation(() => `uuid-${++uuidCounter}` as ReturnType<typeof crypto.randomUUID>);
});

function makeEntry(overrides: Partial<MetricEntryPublic> = {}): MetricEntryPublic {
  return {
    id: "MET-1",
    period_start: "2026-01-01",
    period_end: "2026-01-31",
    channel: "Instagram",
    source: "MANUAL",
    values: { impressions: 1000 },
    is_current: true,
    created_at: "2026-02-01T00:00:00Z",
    ...overrides,
  };
}

async function fillMinimalValidForm(user: ReturnType<typeof userEvent.setup>) {
  fireEvent.change(screen.getByLabelText("Inicio del período"), { target: { value: "2026-01-01" } });
  fireEvent.change(screen.getByLabelText("Fin del período"), { target: { value: "2026-01-31" } });
  await user.type(screen.getByLabelText("Canal"), "Instagram");
  await user.type(screen.getByLabelText("Nombre de la métrica"), "impressions");
  await user.type(screen.getByLabelText("Valor"), "1000");
}

describe("MetricsPanel", () => {
  it("does not fetch while inactive", () => {
    render(<MetricsPanel campaignId="campaign-1" active={false} refreshToken={0} />);
    expect(mockGetMetrics).not.toHaveBeenCalled();
  });

  it("renders the loading state once activated", () => {
    mockGetMetrics.mockImplementation(() => new Promise(() => {}));
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(screen.getByRole("status")).toHaveTextContent("Cargando métricas…");
  });

  it("shows a non-leaky error and retries by issuing the GET again", async () => {
    mockGetMetrics
      .mockRejectedValueOnce(new ApiError(403, "FORBIDDEN", "internal detail should not leak"))
      .mockResolvedValueOnce({ items: [] });
    const user = userEvent.setup();
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);

    expect(await screen.findByText("No encontramos esta campaña, o no tienes acceso a ella.")).toBeInTheDocument();
    expect(screen.queryByText(/internal detail/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /reintentar/i }));
    expect(await screen.findByText(EMPTY_COPY)).toBeInTheDocument();
    expect(mockGetMetrics).toHaveBeenCalledTimes(2);
  });

  it("renders truthful empty copy for a campaign with no metrics", async () => {
    mockGetMetrics.mockResolvedValue({ items: [] });
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByText(EMPTY_COPY)).toBeInTheDocument();
  });

  it("renders populated history with period, channel, values and created date", async () => {
    mockGetMetrics.mockResolvedValue({
      items: [makeEntry({ id: "MET-2", values: { impressions: 500, clicks: 20 } })],
    });
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);

    expect(await screen.findByText("2026-01-01 – 2026-01-31")).toBeInTheDocument();
    expect(screen.getByText("Instagram")).toBeInTheDocument();
    expect(screen.getByText("impressions")).toBeInTheDocument();
    expect(screen.getByText("500")).toBeInTheDocument();
    expect(screen.getByText("clicks")).toBeInTheDocument();
    expect(screen.getByText("20")).toBeInTheDocument();
  });

  it("renders honest source labels for MANUAL, IMPORTED and PLATFORM history entries", async () => {
    mockGetMetrics.mockResolvedValue({
      items: [
        makeEntry({ id: "MET-3", source: "MANUAL" }),
        makeEntry({ id: "MET-4", source: "IMPORTED", period_start: "2026-02-01", period_end: "2026-02-28" }),
        makeEntry({ id: "MET-5", source: "PLATFORM", period_start: "2026-03-01", period_end: "2026-03-31" }),
      ],
    });
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);

    expect(await screen.findByText("Manual")).toBeInTheDocument();
    expect(screen.getByText("Importado")).toBeInTheDocument();
    expect(screen.getByText("Plataforma")).toBeInTheDocument();
  });

  it("frames is_current as descriptive metadata, never as verification/approval", async () => {
    mockGetMetrics.mockResolvedValue({
      items: [
        makeEntry({ id: "MET-6", is_current: true }),
        makeEntry({ id: "MET-7", is_current: false, period_start: "2025-12-01", period_end: "2025-12-31" }),
      ],
    });
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);

    expect(await screen.findByText(CURRENT_COPY)).toBeInTheDocument();
    expect(screen.getByText(PRIOR_COPY)).toBeInTheDocument();
    for (const forbidden of [/verificado/i, /aprobado/i, /inválido/i, /rechazado/i, /incorrecto/i, /mejor registro/i]) {
      expect(screen.queryByText(forbidden)).not.toBeInTheDocument();
    }
  });

  it("exposes no source selector on the create form — only MANUAL is ever submitted", async () => {
    mockGetMetrics.mockResolvedValue({ items: [] });
    mockCreateMetricEntry.mockResolvedValue(makeEntry());
    const user = userEvent.setup();
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);

    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();

    await fillMinimalValidForm(user);
    await user.click(screen.getByRole("button", { name: "Registrar métricas" }));

    await waitFor(() => expect(mockCreateMetricEntry).toHaveBeenCalledTimes(1));
    const payload = mockCreateMetricEntry.mock.calls[0][1] as MetricEntryWriteRequest;
    expect(payload.source).toBe("MANUAL");
  });

  it("requires period start/end, channel, metric name and value before submitting", async () => {
    mockGetMetrics.mockResolvedValue({ items: [] });
    const user = userEvent.setup();
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);

    await user.click(screen.getByRole("button", { name: "Registrar métricas" }));

    expect(await screen.findByText("Ingresa la fecha de inicio.")).toBeInTheDocument();
    expect(screen.getByText("Ingresa la fecha de fin.")).toBeInTheDocument();
    expect(screen.getByText("Ingresa un canal.")).toBeInTheDocument();
    expect(screen.getByText("Ingresa un nombre de métrica.")).toBeInTheDocument();
    expect(screen.getByText("Ingresa un valor.")).toBeInTheDocument();
    expect(mockCreateMetricEntry).not.toHaveBeenCalled();
  });

  it("rejects a period_end before period_start client-side", async () => {
    mockGetMetrics.mockResolvedValue({ items: [] });
    const user = userEvent.setup();
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);

    fireEvent.change(screen.getByLabelText("Inicio del período"), { target: { value: "2026-02-01" } });
    fireEvent.change(screen.getByLabelText("Fin del período"), { target: { value: "2026-01-01" } });
    await user.type(screen.getByLabelText("Canal"), "Instagram");
    await user.type(screen.getByLabelText("Nombre de la métrica"), "impressions");
    await user.type(screen.getByLabelText("Valor"), "10");
    await user.click(screen.getByRole("button", { name: "Registrar métricas" }));

    expect(await screen.findByText("La fecha de fin debe ser igual o posterior a la de inicio.")).toBeInTheDocument();
    expect(mockCreateMetricEntry).not.toHaveBeenCalled();
  });

  it("rejects a non-decimal value client-side", async () => {
    mockGetMetrics.mockResolvedValue({ items: [] });
    const user = userEvent.setup();
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);

    fireEvent.change(screen.getByLabelText("Inicio del período"), { target: { value: "2026-01-01" } });
    fireEvent.change(screen.getByLabelText("Fin del período"), { target: { value: "2026-01-31" } });
    await user.type(screen.getByLabelText("Canal"), "Instagram");
    await user.type(screen.getByLabelText("Nombre de la métrica"), "impressions");
    await user.type(screen.getByLabelText("Valor"), "not-a-number");
    await user.click(screen.getByRole("button", { name: "Registrar métricas" }));

    expect(await screen.findByText(/ingresa un número válido/i)).toBeInTheDocument();
    expect(mockCreateMetricEntry).not.toHaveBeenCalled();
  });

  it("accepts a negative decimal value without a non-negative restriction", async () => {
    mockGetMetrics.mockResolvedValue({ items: [] });
    mockCreateMetricEntry.mockResolvedValue(makeEntry());
    const user = userEvent.setup();
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);

    fireEvent.change(screen.getByLabelText("Inicio del período"), { target: { value: "2026-01-01" } });
    fireEvent.change(screen.getByLabelText("Fin del período"), { target: { value: "2026-01-31" } });
    await user.type(screen.getByLabelText("Canal"), "Instagram");
    await user.type(screen.getByLabelText("Nombre de la métrica"), "adjustment");
    await user.type(screen.getByLabelText("Valor"), "-3.5");
    await user.click(screen.getByRole("button", { name: "Registrar métricas" }));

    await waitFor(() => expect(mockCreateMetricEntry).toHaveBeenCalledTimes(1));
    const payload = mockCreateMetricEntry.mock.calls[0][1] as MetricEntryWriteRequest;
    expect(payload.values.adjustment).toBe("-3.5");
  });

  it("accepts an arbitrary, non-canonical metric name", async () => {
    mockGetMetrics.mockResolvedValue({ items: [] });
    mockCreateMetricEntry.mockResolvedValue(makeEntry());
    const user = userEvent.setup();
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);

    fireEvent.change(screen.getByLabelText("Inicio del período"), { target: { value: "2026-01-01" } });
    fireEvent.change(screen.getByLabelText("Fin del período"), { target: { value: "2026-01-31" } });
    await user.type(screen.getByLabelText("Canal"), "Sitio web");
    await user.type(screen.getByLabelText("Nombre de la métrica"), "reuniones agendadas");
    await user.type(screen.getByLabelText("Valor"), "7");
    await user.click(screen.getByRole("button", { name: "Registrar métricas" }));

    await waitFor(() => expect(mockCreateMetricEntry).toHaveBeenCalledTimes(1));
    const payload = mockCreateMetricEntry.mock.calls[0][1] as MetricEntryWriteRequest;
    expect(payload.values["reuniones agendadas"]).toBe("7");
  });

  it("prevents two metric rows sharing the same name (case-insensitive)", async () => {
    mockGetMetrics.mockResolvedValue({ items: [] });
    const user = userEvent.setup();
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);

    await fillMinimalValidForm(user);
    await user.click(screen.getByRole("button", { name: "Agregar métrica" }));
    const nameInputs = screen.getAllByLabelText("Nombre de la métrica");
    const valueInputs = screen.getAllByLabelText("Valor");
    await user.type(nameInputs[1], "Impressions");
    await user.type(valueInputs[1], "5");

    await user.click(screen.getByRole("button", { name: "Registrar métricas" }));

    expect(await screen.findByText("Ya agregaste una métrica con este nombre.")).toBeInTheDocument();
    expect(mockCreateMetricEntry).not.toHaveBeenCalled();
  });

  it("supports adding and removing dynamic metric rows, always keeping at least one", async () => {
    mockGetMetrics.mockResolvedValue({ items: [] });
    const user = userEvent.setup();
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);

    expect(screen.getAllByLabelText("Nombre de la métrica")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Agregar métrica" }));
    expect(screen.getAllByLabelText("Nombre de la métrica")).toHaveLength(2);

    const removeButtons = screen.getAllByRole("button", { name: /eliminar métrica/i });
    expect(removeButtons[0]).not.toBeDisabled();
    await user.click(removeButtons[0]);
    expect(screen.getAllByLabelText("Nombre de la métrica")).toHaveLength(1);

    const [onlyRemoveButton] = screen.getAllByRole("button", { name: /eliminar métrica/i });
    expect(onlyRemoveButton).toBeDisabled();
  });

  it("submits with the correct campaignId and source MANUAL, then refreshes the list", async () => {
    mockGetMetrics.mockResolvedValue({ items: [] });
    mockCreateMetricEntry.mockResolvedValue(makeEntry());
    const user = userEvent.setup();
    render(<MetricsPanel campaignId="campaign-99" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);

    await fillMinimalValidForm(user);
    await user.click(screen.getByRole("button", { name: "Registrar métricas" }));

    await waitFor(() => expect(mockCreateMetricEntry).toHaveBeenCalledTimes(1));
    expect(mockCreateMetricEntry.mock.calls[0][0]).toBe("campaign-99");
    expect(await screen.findByText(SUCCESS_COPY)).toBeInTheDocument();
    await waitFor(() => expect(mockGetMetrics).toHaveBeenCalledTimes(2));
  });

  it("reuses the same client_request_id when retrying an unresolved submission", async () => {
    mockGetMetrics.mockResolvedValue({ items: [] });
    mockCreateMetricEntry.mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom")).mockResolvedValueOnce(makeEntry());
    const user = userEvent.setup();
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);

    await fillMinimalValidForm(user);
    await user.click(screen.getByRole("button", { name: "Registrar métricas" }));
    await waitFor(() => expect(mockCreateMetricEntry).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "Registrar métricas" }));
    await waitFor(() => expect(mockCreateMetricEntry).toHaveBeenCalledTimes(2));

    const firstId = (mockCreateMetricEntry.mock.calls[0][1] as MetricEntryWriteRequest).client_request_id;
    const secondId = (mockCreateMetricEntry.mock.calls[1][1] as MetricEntryWriteRequest).client_request_id;
    expect(secondId).toBe(firstId);
  });

  it("generates a fresh client_request_id for the next logical entry after a confirmed success", async () => {
    mockGetMetrics.mockResolvedValue({ items: [] });
    mockCreateMetricEntry.mockResolvedValue(makeEntry());
    const user = userEvent.setup();
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);

    await fillMinimalValidForm(user);
    await user.click(screen.getByRole("button", { name: "Registrar métricas" }));
    await waitFor(() => expect(mockCreateMetricEntry).toHaveBeenCalledTimes(1));

    await fillMinimalValidForm(user);
    await user.click(screen.getByRole("button", { name: "Registrar métricas" }));
    await waitFor(() => expect(mockCreateMetricEntry).toHaveBeenCalledTimes(2));

    const firstId = (mockCreateMetricEntry.mock.calls[0][1] as MetricEntryWriteRequest).client_request_id;
    const secondId = (mockCreateMetricEntry.mock.calls[1][1] as MetricEntryWriteRequest).client_request_id;
    expect(secondId).not.toBe(firstId);
  });

  it("disables the submit button while the request is pending", async () => {
    mockGetMetrics.mockResolvedValue({ items: [] });
    let resolveCreate: (value: MetricEntryPublic) => void = () => {};
    mockCreateMetricEntry.mockImplementation(() => new Promise((resolve) => { resolveCreate = resolve; }));
    const user = userEvent.setup();
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(EMPTY_COPY);

    await fillMinimalValidForm(user);
    await user.click(screen.getByRole("button", { name: "Registrar métricas" }));

    expect(await screen.findByRole("button", { name: /registrando/i })).toBeDisabled();
    resolveCreate(makeEntry());
    await waitFor(() => expect(screen.getByRole("button", { name: "Registrar métricas" })).not.toBeDisabled());
  });

  it("exposes no correction (PUT) control anywhere in the panel", async () => {
    mockGetMetrics.mockResolvedValue({ items: [makeEntry()] });
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("Instagram");

    for (const forbidden of [/^editar$/i, /corregir/i, /reemplazar/i]) {
      expect(screen.queryByRole("button", { name: forbidden })).not.toBeInTheDocument();
    }
  });

  it("never renders derived metrics, provider branding, or Tracking/Learning/Strategy inference", async () => {
    mockGetMetrics.mockResolvedValue({
      items: [makeEntry({ source: "IMPORTED" }), makeEntry({ id: "MET-x", source: "PLATFORM" })],
    });
    render(<MetricsPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findAllByText("Instagram");

    for (const forbidden of [
      /CTR/,
      /CPC/,
      /CPA/,
      /ROAS/i,
      /tasa de conversión/i,
      /Meta Ads/i,
      /Google Analytics/i,
      /TikTok/i,
      /conectar proveedor/i,
      /verificado/i,
      /certificado/i,
      /pixel/i,
      /aprendizaje/i,
      /recomendación estratégica/i,
      /decisión estratégica/i,
      /análisis completado/i,
      /señal detectada/i,
    ]) {
      expect(screen.queryByText(forbidden)).not.toBeInTheDocument();
    }
  });
});

describe("MetricsPanel integration inside CampaignDetail", () => {
  it("replaces the static Métricas placeholder with the real panel and uses the correct campaignId", async () => {
    mockGetCampaign.mockResolvedValue({
      id: "campaign-42",
      name: "Campaña de prueba",
      status: "DRAFT",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      archived_at: null,
    });
    mockListCampaignRuns.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    mockGetMetrics.mockResolvedValue({ items: [] });

    const user = userEvent.setup();
    render(<CampaignDetail campaignId="campaign-42" />);

    await screen.findByRole("heading", { name: "Campaña de prueba" });
    await user.click(screen.getByRole("tab", { name: "Métricas" }));

    expect(await screen.findByText(EMPTY_COPY)).toBeInTheDocument();
    expect(mockGetMetrics).toHaveBeenCalledWith("campaign-42");
    expect(screen.queryByText("Métricas aún no disponibles")).not.toBeInTheDocument();
  });
});
