import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AnalysisPanel } from "@/components/campaigns/detail/analysis-panel";
import { ApiError } from "@/lib/api/client";
import type {
  AnalysisResponse,
  MeasurementAnalysisRunPublic,
  MeasurementAnalysisRunTriggerRequest,
} from "@/types/measurement";

vi.mock("@/lib/api/measurement", () => ({
  getCampaignAnalysis: vi.fn(),
  triggerCampaignAnalysis: vi.fn(),
}));

import { getCampaignAnalysis, triggerCampaignAnalysis } from "@/lib/api/measurement";

const mockGetCampaignAnalysis = vi.mocked(getCampaignAnalysis);
const mockTriggerCampaignAnalysis = vi.mocked(triggerCampaignAnalysis);

const NEUTRAL_EMPTY_TITLE = "No hay resultados de análisis disponibles con los datos actuales.";
const COMPLETED_EMPTY_COPY = "El análisis se ejecutó, pero no encontró observaciones ni señales con los datos actuales.";
const COMPLETED_SUCCESS_COPY = "Análisis actualizado con las métricas disponibles.";
const RUNNING_COPY = "Hay un análisis en curso para esta campaña. Esta página no se actualiza automáticamente.";
const CONFLICT_COPY = "No se pudo iniciar el análisis en este momento. Intenta de nuevo.";
const REFETCH_FAILURE_COPY = "No pudimos actualizar los resultados del análisis.";
const NO_SIGNALS_COPY = "No se detectaron señales comparativas con los datos actuales.";
const NO_RESULT_COPY = "No se generó un resultado de análisis para esta ejecución.";

let uuidCounter = 0;

beforeEach(() => {
  vi.clearAllMocks();
  uuidCounter = 0;
  vi.spyOn(crypto, "randomUUID").mockImplementation(() => `uuid-${++uuidCounter}` as ReturnType<typeof crypto.randomUUID>);
});

function emptyAnalysis(): AnalysisResponse {
  return { observations: [], signals: [], analysis_results: [] };
}

function makeRun(overrides: Partial<MeasurementAnalysisRunPublic> = {}): MeasurementAnalysisRunPublic {
  return {
    id: "MAR-1",
    campaign_id: "campaign-1",
    client_request_id: "uuid-1",
    status: "COMPLETED",
    failure_reason: null,
    created_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:00:05Z",
    ...overrides,
  };
}

function lastTriggerKey(): string {
  const calls = mockTriggerCampaignAnalysis.mock.calls;
  const payload = calls[calls.length - 1][1] as MeasurementAnalysisRunTriggerRequest;
  return payload.client_request_id;
}

async function clickTrigger(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /analizar métricas|analizando/i }));
}

describe("AnalysisPanel — fetch / render", () => {
  it("does not fetch while inactive", () => {
    render(<AnalysisPanel campaignId="campaign-1" active={false} refreshToken={0} />);
    expect(mockGetCampaignAnalysis).not.toHaveBeenCalled();
  });

  it("loads GET /analysis once activated", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await waitFor(() => expect(mockGetCampaignAnalysis).toHaveBeenCalledWith("campaign-1"));
  });

  it("renders the loading state", () => {
    mockGetCampaignAnalysis.mockImplementation(() => new Promise(() => {}));
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(screen.getByText("Cargando análisis…")).toBeInTheDocument();
  });

  it("shows neutral empty copy on initial all-empty response", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByText(NEUTRAL_EMPTY_TITLE)).toBeInTheDocument();
  });

  it("never claims analysis was never executed on initial empty response", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);
    expect(screen.queryByText(/aún no se ha ejecutado/i)).not.toBeInTheDocument();
    expect(screen.queryByText(COMPLETED_EMPTY_COPY)).not.toBeInTheDocument();
  });

  it("renders observations", async () => {
    mockGetCampaignAnalysis.mockResolvedValue({
      observations: [{ id: "OBS-1", metric_name: "clicks", value: 100, source_metric_entry_ids: ["MET-1"], created_at: "2026-01-01T00:00:00Z" }],
      signals: [],
      analysis_results: [],
    });
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByText("clicks")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();
  });

  it("renders signals", async () => {
    mockGetCampaignAnalysis.mockResolvedValue({
      observations: [{ id: "OBS-1", metric_name: "clicks", value: 100, source_metric_entry_ids: [], created_at: "2026-01-01T00:00:00Z" }],
      signals: [{ id: "SIG-1", summary: "clicks increased for Instagram: 100 to 150.", source_observation_ids: ["OBS-1"], created_at: "2026-01-01T00:00:00Z" }],
      analysis_results: [],
    });
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByText("clicks increased for Instagram: 100 to 150.")).toBeInTheDocument();
  });

  it("renders analysis results", async () => {
    mockGetCampaignAnalysis.mockResolvedValue({
      observations: [{ id: "OBS-1", metric_name: "clicks", value: 100, source_metric_entry_ids: [], created_at: "2026-01-01T00:00:00Z" }],
      signals: [{ id: "SIG-1", summary: "clicks increased.", source_observation_ids: [], created_at: "2026-01-01T00:00:00Z" }],
      analysis_results: [{ id: "ANL-1", summary: "Analysis run MAR-1 identified 1 performance signal(s): clicks increased.", source_signal_ids: ["SIG-1"], created_at: "2026-01-01T00:00:00Z" }],
    });
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByText(/Analysis run MAR-1 identified/)).toBeInTheDocument();
  });

  it("shows a distinct empty state for observations without signals", async () => {
    mockGetCampaignAnalysis.mockResolvedValue({
      observations: [{ id: "OBS-1", metric_name: "clicks", value: 100, source_metric_entry_ids: [], created_at: "2026-01-01T00:00:00Z" }],
      signals: [],
      analysis_results: [],
    });
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByText(NO_SIGNALS_COPY)).toBeInTheDocument();
  });

  it("shows a distinct empty state for signals without an analysis result", async () => {
    mockGetCampaignAnalysis.mockResolvedValue({
      observations: [{ id: "OBS-1", metric_name: "clicks", value: 100, source_metric_entry_ids: [], created_at: "2026-01-01T00:00:00Z" }],
      signals: [{ id: "SIG-1", summary: "clicks increased.", source_observation_ids: [], created_at: "2026-01-01T00:00:00Z" }],
      analysis_results: [],
    });
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByText(NO_RESULT_COPY)).toBeInTheDocument();
  });
});

describe("AnalysisPanel — trigger", () => {
  it("calls POST only after an explicit click, never on mount or activation", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);
    expect(mockTriggerCampaignAnalysis).not.toHaveBeenCalled();
  });

  it("clicking Analizar métricas calls the trigger endpoint with the campaign id", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun());
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1));
    expect(mockTriggerCampaignAnalysis.mock.calls[0][0]).toBe("campaign-1");
  });

  it("generates a plausible, non-empty client_request_id", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun());
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1));
    const key = lastTriggerKey();
    expect(typeof key).toBe("string");
    expect(key.length).toBeGreaterThan(0);
  });

  it("disables the button and sets aria-busy while the request is pending", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    let resolveTrigger: (value: MeasurementAnalysisRunPublic) => void = () => {};
    mockTriggerCampaignAnalysis.mockImplementation(() => new Promise((resolve) => { resolveTrigger = resolve; }));
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    const pendingButton = await screen.findByRole("button", { name: /analizando/i });
    expect(pendingButton).toBeDisabled();
    expect(pendingButton).toHaveAttribute("aria-busy", "true");

    resolveTrigger(makeRun());
    await waitFor(() => expect(screen.getByRole("button", { name: "Analizar métricas" })).not.toBeDisabled());
  });

  it("renders COMPLETED success copy", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun({ status: "COMPLETED" }));
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    expect(await screen.findByText(COMPLETED_SUCCESS_COPY)).toBeInTheDocument();
  });

  it("refetches GET /analysis after COMPLETED", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun({ status: "COMPLETED" }));
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await waitFor(() => expect(mockGetCampaignAnalysis).toHaveBeenCalledTimes(2));
  });

  it("keeps COMPLETED outcome and prior evidence when the analysis refetch fails", async () => {
    const priorEvidence: AnalysisResponse = {
      observations: [{ id: "OBS-1", metric_name: "clicks", value: 100, source_metric_entry_ids: [], created_at: "2026-01-01T00:00:00Z" }],
      signals: [],
      analysis_results: [],
    };
    const refreshedEvidence: AnalysisResponse = {
      observations: [{ id: "OBS-2", metric_name: "impressions", value: 500, source_metric_entry_ids: [], created_at: "2026-01-02T00:00:00Z" }],
      signals: [],
      analysis_results: [],
    };
    mockGetCampaignAnalysis
      .mockResolvedValueOnce(priorEvidence) // initial load
      .mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom")) // refetch after COMPLETED
      .mockResolvedValueOnce(refreshedEvidence); // explicit read retry
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun({ status: "COMPLETED" }));
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("clicks");

    await clickTrigger(user);

    // F: COMPLETED success copy remains visible.
    expect(await screen.findByText(COMPLETED_SUCCESS_COPY)).toBeInTheDocument();
    // G: prior evidence remains rendered — the refetch failure never clears it.
    expect(screen.getByText("clicks")).toBeInTheDocument();
    // H: a distinct read/refetch error is rendered via the existing UI.
    expect(await screen.findByText(REFETCH_FAILURE_COPY)).toBeInTheDocument();
    // I: no domain FAILED misclassification — no failure_reason/generic
    // failure copy appears merely because the read failed.
    expect(screen.queryByText("El análisis no se pudo completar.")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert", { name: /análisis no se pudo completar/i })).not.toBeInTheDocument();

    // J: exactly one trigger call, two getCampaignAnalysis calls so far
    // (initial load + the COMPLETED refetch attempt) — no automatic extra GET.
    expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1);
    expect(mockGetCampaignAnalysis).toHaveBeenCalledTimes(2);
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(mockGetCampaignAnalysis).toHaveBeenCalledTimes(2);
    expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1);

    // K: the retry affordance re-reads evidence without re-triggering a run.
    await user.click(screen.getByRole("button", { name: "Reintentar" }));
    await waitFor(() => expect(mockGetCampaignAnalysis).toHaveBeenCalledTimes(3));
    expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("impressions")).toBeInTheDocument();
    expect(screen.queryByText(REFETCH_FAILURE_COPY)).not.toBeInTheDocument();
  });

  it("renders FAILED failure_reason verbatim", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(
      makeRun({ status: "FAILED", failure_reason: "No se pudo completar el análisis en este momento." }),
    );
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    expect(await screen.findByText("No se pudo completar el análisis en este momento.")).toBeInTheDocument();
  });

  it("does not refetch GET /analysis after FAILED", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun({ status: "FAILED", failure_reason: "boom (sanitized)" }));
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await screen.findByText("boom (sanitized)");
    expect(mockGetCampaignAnalysis).toHaveBeenCalledTimes(1);
  });

  it("preserves prior evidence when a later trigger FAILS", async () => {
    mockGetCampaignAnalysis.mockResolvedValue({
      observations: [{ id: "OBS-1", metric_name: "clicks", value: 100, source_metric_entry_ids: [], created_at: "2026-01-01T00:00:00Z" }],
      signals: [],
      analysis_results: [],
    });
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun({ status: "FAILED", failure_reason: "no se pudo completar" }));
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("clicks");

    await clickTrigger(user);
    await screen.findByText("no se pudo completar");
    expect(screen.getByText("clicks")).toBeInTheDocument();
  });

  it("renders a static RUNNING notice", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun({ status: "RUNNING" }));
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    expect(await screen.findByText(RUNNING_COPY)).toBeInTheDocument();
  });

  it("causes no polling/timer-driven API calls after RUNNING", async () => {
    // Proves the absence of any polling behavior from this component's own
    // code: after RUNNING is observed, a real (short) wait passes with the
    // trigger/read call counts staying exactly where they landed — no
    // setInterval/recursive fetch loop lives in AnalysisPanel itself.
    // (vi.useFakeTimers() is deliberately avoided here — it does not mix
    // safely with @testing-library/user-event's own internal scheduling
    // and testing-library's own findBy*/waitFor helpers use a real
    // setInterval internally, which a blanket spy would also catch.)
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun({ status: "RUNNING" }));
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await screen.findByText(RUNNING_COPY);

    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1);
    expect(mockGetCampaignAnalysis).toHaveBeenCalledTimes(1);
  });
});

describe("AnalysisPanel — client_request_id lifecycle", () => {
  it("NETWORK_ERROR preserves the same key on the next deliberate retry", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis
      .mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR", "unreachable"))
      .mockResolvedValueOnce(makeRun());
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1));
    const firstKey = lastTriggerKey();

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(2));
    expect(lastTriggerKey()).toBe(firstKey);
  });

  it("HTTP 500 preserves the same key on the next deliberate retry", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis
      .mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom"))
      .mockResolvedValueOnce(makeRun());
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1));
    const firstKey = lastTriggerKey();

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(2));
    expect(lastTriggerKey()).toBe(firstKey);
  });

  it("HTTP 401 preserves the same key on the next deliberate retry", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis
      .mockRejectedValueOnce(new ApiError(401, "AUTHENTICATION_REQUIRED", "no session"))
      .mockResolvedValueOnce(makeRun());
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1));
    const firstKey = lastTriggerKey();

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(2));
    expect(lastTriggerKey()).toBe(firstKey);
  });

  it("HTTP 403 CSRF_INVALID preserves the same key on the next deliberate retry", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis
      .mockRejectedValueOnce(new ApiError(403, "CSRF_INVALID", "stale token"))
      .mockResolvedValueOnce(makeRun());
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1));
    const firstKey = lastTriggerKey();

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(2));
    expect(lastTriggerKey()).toBe(firstKey);
  });

  it("HTTP 403 FORBIDDEN preserves the same key on the next deliberate retry", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis
      .mockRejectedValueOnce(new ApiError(403, "FORBIDDEN", "no access"))
      .mockResolvedValueOnce(makeRun());
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1));
    const firstKey = lastTriggerKey();

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(2));
    expect(lastTriggerKey()).toBe(firstKey);
  });

  it("HTTP 422 preserves the same key on the next deliberate retry", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis
      .mockRejectedValueOnce(new ApiError(422, "VALIDATION_ERROR", "invalid body"))
      .mockResolvedValueOnce(makeRun());
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1));
    const firstKey = lastTriggerKey();

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(2));
    expect(lastTriggerKey()).toBe(firstKey);
  });

  it("HTTP 409 IDEMPOTENCY_KEY_CONFLICT rotates the key on the next deliberate click", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis
      .mockRejectedValueOnce(new ApiError(409, "IDEMPOTENCY_KEY_CONFLICT", "collision"))
      .mockResolvedValueOnce(makeRun());
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1));
    const firstKey = lastTriggerKey();

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(2));
    expect(lastTriggerKey()).not.toBe(firstKey);
  });

  it("COMPLETED rotates the key on the next deliberate click", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun({ status: "COMPLETED" }));
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1));
    const firstKey = lastTriggerKey();

    await waitFor(() => expect(screen.getByRole("button", { name: "Analizar métricas" })).not.toBeDisabled());
    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(2));
    expect(lastTriggerKey()).not.toBe(firstKey);
  });

  it("FAILED rotates the key on the next deliberate click", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun({ status: "FAILED", failure_reason: "boom" }));
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1));
    const firstKey = lastTriggerKey();

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(2));
    expect(lastTriggerKey()).not.toBe(firstKey);
  });

  it("RUNNING: no automatic retry, and the next NEW deliberate action uses a new key", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun({ status: "RUNNING" }));
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(1));
    const firstKey = lastTriggerKey();

    await clickTrigger(user);
    await waitFor(() => expect(mockTriggerCampaignAnalysis).toHaveBeenCalledTimes(2));
    expect(lastTriggerKey()).not.toBe(firstKey);
  });
});

describe("AnalysisPanel — session epistemics", () => {
  it("shows completed-empty copy only after this session observed COMPLETED then an empty refetch", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun({ status: "COMPLETED" }));
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    expect(await screen.findByText(COMPLETED_EMPTY_COPY)).toBeInTheDocument();
    expect(screen.queryByText(NEUTRAL_EMPTY_TITLE)).not.toBeInTheDocument();
  });

  it("a fresh remount with an empty GET falls back to the neutral copy, never completed-empty", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun({ status: "COMPLETED" }));
    const user = userEvent.setup();
    const { unmount } = render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);
    await clickTrigger(user);
    await screen.findByText(COMPLETED_EMPTY_COPY);
    unmount();

    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    expect(await screen.findByText(NEUTRAL_EMPTY_TITLE)).toBeInTheDocument();
    expect(screen.queryByText(COMPLETED_EMPTY_COPY)).not.toBeInTheDocument();
  });

  it("does not reconstruct prior trigger status on remount (no local/session storage involved)", async () => {
    const storageSpy = vi.spyOn(Storage.prototype, "setItem");
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockResolvedValue(makeRun({ status: "FAILED", failure_reason: "boom" }));
    const user = userEvent.setup();
    const { unmount } = render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);
    await clickTrigger(user);
    await screen.findByText("boom");
    unmount();

    expect(storageSpy).not.toHaveBeenCalled();
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);
    expect(screen.queryByText("boom")).not.toBeInTheDocument();
    storageSpy.mockRestore();
  });
});

describe("AnalysisPanel — 409 safety", () => {
  it("renders the generic safe 409 copy and leaks no backend internals", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    mockTriggerCampaignAnalysis.mockRejectedValue(
      new ApiError(409, "IDEMPOTENCY_KEY_CONFLICT", "client_request_id has already been used for another operation."),
    );
    const user = userEvent.setup();
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);

    await clickTrigger(user);
    expect(await screen.findByText(CONFLICT_COPY)).toBeInTheDocument();
    expect(screen.queryByText(/IDEMPOTENCY_KEY_CONFLICT/)).not.toBeInTheDocument();
    expect(screen.queryByText(/has already been used/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/MAR-/)).not.toBeInTheDocument();
    expect(screen.queryByText(/CMP-/)).not.toBeInTheDocument();
  });
});

describe("AnalysisPanel — freezes", () => {
  it("never renders recommendation, Learning, winner, or strategy-mutation vocabulary", async () => {
    mockGetCampaignAnalysis.mockResolvedValue({
      observations: [{ id: "OBS-1", metric_name: "clicks", value: 100, source_metric_entry_ids: [], created_at: "2026-01-01T00:00:00Z" }],
      signals: [{ id: "SIG-1", summary: "clicks increased for Instagram: 100 to 150.", source_observation_ids: [], created_at: "2026-01-01T00:00:00Z" }],
      analysis_results: [{ id: "ANL-1", summary: "Analysis run MAR-1 identified 1 performance signal(s): clicks increased.", source_signal_ids: [], created_at: "2026-01-01T00:00:00Z" }],
    });
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={0} />);
    await screen.findByText("clicks");

    for (const forbidden of [
      /recomendación/i,
      /aprendizaje/i,
      /ganador/i,
      /mejor canal/i,
      /probado/i,
      /decisión estratégica/i,
      /causal/i,
      /validado/i,
    ]) {
      expect(screen.queryByText(forbidden)).not.toBeInTheDocument();
    }
  });

  it("never triggers automatically after activation (no automatic POST)", async () => {
    mockGetCampaignAnalysis.mockResolvedValue(emptyAnalysis());
    render(<AnalysisPanel campaignId="campaign-1" active refreshToken={1} />);
    await screen.findByText(NEUTRAL_EMPTY_TITLE);
    expect(mockTriggerCampaignAnalysis).not.toHaveBeenCalled();
  });
});
