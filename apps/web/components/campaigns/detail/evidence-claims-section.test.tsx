import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EvidenceClaimsSection } from "@/components/campaigns/detail/evidence-claims-section";
import { ApiError } from "@/lib/api/client";
import type { MetricEntryListResponse } from "@/types/measurement";
import type { EvidenceClaimListResponse, EvidenceClaimPublic, MeasurementContractPublic } from "@/types/strategy";

// Experiment Evidence Binding — component tests for the claims section. These are COMPONENT tests with a mocked
// API client; they are NOT browser validation.

vi.mock("@/lib/api/strategy", () => ({
  createEvidenceClaim: vi.fn(),
  disposeEvidenceClaim: vi.fn(),
  getMeasurementContract: vi.fn(),
  listEvidenceClaims: vi.fn(),
}));
vi.mock("@/lib/api/measurement", () => ({
  getMetrics: vi.fn(),
}));

import {
  createEvidenceClaim,
  disposeEvidenceClaim,
  getMeasurementContract,
  listEvidenceClaims,
} from "@/lib/api/strategy";
import { getMetrics } from "@/lib/api/measurement";

const mockList = vi.mocked(listEvidenceClaims);
const mockCreate = vi.mocked(createEvidenceClaim);
const mockDispose = vi.mocked(disposeEvidenceClaim);
const mockContract = vi.mocked(getMeasurementContract);
const mockMetrics = vi.mocked(getMetrics);

let uuidCounter = 0;

function claim(overrides: Partial<EvidenceClaimPublic> = {}): EvidenceClaimPublic {
  return {
    id: "ECL-1",
    experiment_id: "EXP-1",
    semantics: "PROVENANCE CLAIM ONLY — NOT ELIGIBILITY OR VALIDATION",
    scope: "EXPERIMENT_LEVEL",
    reporter_note: "note",
    claimed_by: "USR-A",
    created_at: "2026-03-02T09:00:00Z",
    is_disposed: false,
    disposed_at: null,
    disposed_by: null,
    disposal_reason: null,
    authorization: { id: "EXA-1", revoked_at: null, revoked_reason: null },
    start: { id: "EXS-1", started_at: "2026-03-01T10:30:00Z" },
    required_signal: {
      id: "RSG-1",
      name: "Tasa de clics",
      expected_direction: null,
      tracking_required: false,
      contract_version_id: "MSC-1",
      contract_version: 1,
    },
    datum: {
      metric_entry_id: "MET-1",
      metric_name: "clicks",
      value: "50.0000",
      period_start: "2026-01-01",
      period_end: "2026-01-31",
      channel: "Instagram",
      source: "MANUAL",
      entry_created_at: "2026-02-01T00:00:00Z",
    },
    distribution: null,
    later_correction_exists: false,
    excluded_from_aggregate_and_analysis: false,
    ...overrides,
  };
}

function listing(claims: EvidenceClaimPublic[]): EvidenceClaimListResponse {
  return { experiment_id: "EXP-1", start_id: "EXS-1", claims };
}

const CONTRACT = {
  id: "MSC-1",
  experiment_id: "EXP-1",
  definition_version_id: "EXD-1",
  version: 1,
  measurement_window_days: null,
  minimum_evidence: null,
  success_criterion: null,
  analysis_method_intent: null,
  stopping_rule: null,
  decision_rule_intent: null,
  declaration_level: null,
  declaration_semantics_version: null,
  baseline_window_days: null,
  signals: [
    {
      id: "RSG-1",
      ordinal: 1,
      name: "Tasa de clics",
      description: "d",
      expected_direction: null,
      evidence_requirement: null,
      tracking_required: true,
      bound_metric_name: null,
      channel_binding: null,
      bound_channel: null,
      min_data_points: null,
    },
  ],
  created_at: "2026-01-01T00:00:00Z",
} satisfies MeasurementContractPublic;

const METRICS: MetricEntryListResponse = {
  items: [
    {
      id: "MET-1",
      period_start: "2026-01-01",
      period_end: "2026-01-31",
      channel: "Instagram",
      source: "MANUAL",
      values: { clicks: "50.0000", impressions: "1000.0000" },
      is_current: true,
      created_at: "2026-02-01T00:00:00Z",
    },
    {
      id: "MET-0",
      period_start: "2026-01-01",
      period_end: "2026-01-31",
      channel: "Instagram",
      source: "MANUAL",
      values: { clicks: "40.0000" },
      is_current: false,
      created_at: "2026-01-15T00:00:00Z",
    },
  ],
};

function renderSection(role: string | null = "MEMBER") {
  return render(<EvidenceClaimsSection campaignId="campaign-1" experimentId="EXP-1" startId="EXS-1" role={role} />);
}

async function openCreate() {
  await userEvent.click(await screen.findByText("Registrar afirmación de evidencia"));
  await waitFor(() => expect(mockMetrics).toHaveBeenCalled());
  await screen.findByRole("option", { name: /MET-1 · Instagram/ });
}

async function fillCreate(entryLabel: RegExp = /MET-1 · Instagram/, metric = "clicks") {
  await userEvent.selectOptions(screen.getByLabelText("Señal requerida"), "RSG-1");
  await userEvent.selectOptions(screen.getByLabelText("Entrada de métricas"), screen.getByRole("option", { name: entryLabel }));
  await userEvent.selectOptions(screen.getByLabelText("Métrica"), metric);
}

beforeEach(() => {
  vi.clearAllMocks();
  // clearAllMocks keeps queued `...Once` results; reset the writers so nothing leaks between tests.
  mockCreate.mockReset();
  mockDispose.mockReset();
  mockList.mockResolvedValue(listing([]));
  mockContract.mockResolvedValue(CONTRACT);
  mockMetrics.mockResolvedValue(METRICS);
  uuidCounter = 0;
  vi.spyOn(crypto, "randomUUID").mockImplementation(() => `uuid-${++uuidCounter}` as ReturnType<typeof crypto.randomUUID>);
});

describe("Evidence claims — persistent semantics", () => {
  it("shows the provenance-only warning with an empty list, with claims and for a read-only role", async () => {
    const { unmount } = renderSection();
    expect(await screen.findByText(/Aún no hay afirmaciones de evidencia/)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/SOLO AFIRMACIÓN DE PROCEDENCIA — NO ES ELEGIBILIDAD NI VALIDACIÓN/);
    unmount();
    mockList.mockResolvedValue(listing([claim()]));
    const second = renderSection("VIEWER");
    await screen.findByText("ECL-1");
    expect(screen.getByRole("note")).toHaveTextContent(/NO ES ELEGIBILIDAD NI VALIDACIÓN/);
    second.unmount();
  });

  it("keeps the warning visible while the create and dispose panels are open", async () => {
    mockList.mockResolvedValue(listing([claim()]));
    renderSection();
    await userEvent.click(await screen.findByText("Descartar afirmación ECL-1"));
    expect(screen.getByRole("note")).toBeInTheDocument();
    await userEvent.click(screen.getByText("Cancelar"));
    await openCreate();
    expect(screen.getByRole("note")).toHaveTextContent(/SOLO AFIRMACIÓN DE PROCEDENCIA/);
  });

  it("states the claim is experiment-level, not eligibility/validity/attribution, and names the claimant honestly", async () => {
    renderSection();
    const warning = (await screen.findByRole("note")).textContent ?? "";
    expect(warning).toMatch(/nivel de experimento|bajo este inicio atestiguado/);
    expect(warning).toMatch(/No significa que el dato sea elegible, vigente, suficiente ni correcto/);
    expect(warning).toMatch(/asignación, exposición, atribución a una condición/);
    expect(warning).toMatch(/resultado, ganador/);
    expect(warning).toMatch(/causalidad/);
    expect(warning).toMatch(/no necesariamente quien reportó originalmente la métrica/);
  });
});

describe("Evidence claims — list and disposed history", () => {
  it("shows the empty state and requests the claims of exactly this Start", async () => {
    renderSection();
    expect(await screen.findByText(/Aún no hay afirmaciones de evidencia para este inicio/)).toBeInTheDocument();
    expect(mockList).toHaveBeenCalledWith("campaign-1", "EXP-1", "EXS-1");
  });

  it("renders the literal facts side by side without deriving any eligibility from them", async () => {
    mockList.mockResolvedValue(
      listing([
        claim({
          required_signal: { ...claim().required_signal!, tracking_required: true },
          authorization: { id: "EXA-1", revoked_at: "2026-03-05T00:00:00Z", revoked_reason: "Pausa." },
        }),
      ]),
    );
    renderSection();
    const item = (await screen.findByText("ECL-1")).closest("li") as HTMLElement;
    expect(item).toHaveTextContent(/Tasa de clics \(RSG-1\)/);
    expect(item).toHaveTextContent(/seguimiento declarado: sí \(informativo; no indica que exista o esté validado\)/);
    expect(item).toHaveTextContent(/clicks = 50\.0000 · entrada MET-1/);
    expect(item).toHaveTextContent(/2026-01-01 – 2026-01-31 · canal Instagram · fuente MANUAL/);
    expect(item).toHaveTextContent(/Inicio atestiguado/);
    expect(item).toHaveTextContent(/se muestra junto al periodo; no se compara/);
    expect(item).toHaveTextContent(/USR-A/);
    expect(item).toHaveTextContent(/EXA-1 revocada el .* — Pausa\./);
    expect(item).toHaveTextContent(/activa/);
  });

  it("keeps disposed claims visible with who, when and why, and offers no dispose action for them", async () => {
    mockList.mockResolvedValue(
      listing([
        claim({
          is_disposed: true,
          disposed_at: "2026-03-03T00:00:00Z",
          disposed_by: "USR-B",
          disposal_reason: "Métrica equivocada.",
        }),
        claim({ id: "ECL-2" }),
      ]),
    );
    renderSection();
    const disposed = (await screen.findByText("ECL-1")).closest("li") as HTMLElement;
    expect(disposed).toHaveTextContent(/descartada/);
    expect(disposed).toHaveTextContent(/por USR-B — Métrica equivocada\./);
    expect(disposed).toHaveTextContent(/no significa que la afirmación no ocurriera/);
    expect(screen.queryByText("Descartar afirmación ECL-1")).not.toBeInTheDocument();
    expect(screen.getByText("Descartar afirmación ECL-2")).toBeInTheDocument();
  });

  it("presents a later correction as a read-time observation, never a status", async () => {
    mockList.mockResolvedValue(listing([claim({ later_correction_exists: true })]));
    renderSection();
    const item = (await screen.findByText("ECL-1")).closest("li") as HTMLElement;
    expect(item).toHaveTextContent(/existe una corrección posterior de este dato/);
    expect(item).toHaveTextContent(/sigue apuntando a la entrada original/);
    expect(item).toHaveTextContent(/observación de lectura, no un estado/);
  });

  it("renders a distribution-owned claim with its distribution context and the pipeline limitation", async () => {
    mockList.mockResolvedValue(
      listing([
        claim({
          distribution: {
            distribution_id: "DST-1",
            evidence_id: "DME-2",
            source_reference: "informe-42",
            supersedes_evidence_id: "DME-1",
            superseded_by_evidence_id: null,
            correction_reason: "Corregida.",
          },
          excluded_from_aggregate_and_analysis: true,
        }),
      ]),
    );
    renderSection();
    const item = (await screen.findByText("ECL-1")).closest("li") as HTMLElement;
    expect(item).toHaveTextContent(/DME-2 · distribución DST-1 · referencia informe-42 · corrige DME-1/);
    expect(item).toHaveTextContent(/excluida del listado agregado y del análisis genérico/);
  });

  it("translates a list failure and does not show the empty state", async () => {
    mockList.mockRejectedValue(new ApiError(0, "NETWORK_ERROR", "offline"));
    renderSection();
    expect(await screen.findByRole("alert")).toHaveTextContent(/No pudimos conectar con el servidor/);
    expect(screen.queryByText(/Aún no hay afirmaciones/)).not.toBeInTheDocument();
  });
});

describe("Evidence claims — semantic firewall", () => {
  it("has no accept/verify/qualify/Variant/assignment/exposure/result control and no free-text note", async () => {
    mockList.mockResolvedValue(listing([claim(), claim({ id: "ECL-2", is_disposed: true, disposal_reason: "x" })]));
    const { container } = renderSection();
    await screen.findByText("ECL-1");
    for (const button of screen.getAllByRole("button")) {
      expect(button.textContent ?? "").not.toMatch(
        /acept|verific|validar|calific|elegib|condici[oó]n|variante|asign|expon|resultado|ganador|atribu/i,
      );
    }
    await openCreateFromList();
    expect(container.querySelectorAll("textarea")).toHaveLength(0); // no note field
    expect(screen.getAllByRole("combobox")).toHaveLength(3); // signal, entry, metric — nothing else
  });

  it("labels each claim only as active or disposed — never with an eligibility or validity status", async () => {
    mockList.mockResolvedValue(listing([claim(), claim({ id: "ECL-2", is_disposed: true, disposal_reason: "x" })]));
    renderSection();
    for (const id of ["ECL-1", "ECL-2"]) {
      const item = (await screen.findByText(id)).closest("li") as HTMLElement;
      expect(item.textContent ?? "").toMatch(new RegExp(`^${id} · (activa|descartada)`));
      expect(item.textContent ?? "").not.toMatch(/\b(elegible|aceptada|verificada|calificada|válida|inválida)\b/i);
    }
  });
});

async function openCreateFromList() {
  await userEvent.click(screen.getByText("Registrar afirmación de evidencia"));
  await waitFor(() => expect(mockMetrics).toHaveBeenCalled());
  await screen.findByRole("option", { name: /MET-1 · Instagram/ });
}

describe("Evidence claims — availability", () => {
  it("offers create and dispose to MEMBER+ only", async () => {
    mockList.mockResolvedValue(listing([claim()]));
    const { unmount } = renderSection("MEMBER");
    expect(await screen.findByText("Registrar afirmación de evidencia")).toBeInTheDocument();
    expect(screen.getByText("Descartar afirmación ECL-1")).toBeInTheDocument();
    unmount();
    renderSection("VIEWER");
    await screen.findByText("ECL-1");
    expect(screen.queryByText("Registrar afirmación de evidencia")).not.toBeInTheDocument();
    expect(screen.queryByText(/Descartar afirmación/)).not.toBeInTheDocument();
  });

  it("does not load the signal or metric pickers until the create form is opened", async () => {
    renderSection();
    await screen.findByText(/Aún no hay afirmaciones/);
    expect(mockContract).not.toHaveBeenCalled();
    expect(mockMetrics).not.toHaveBeenCalled();
  });
});

describe("Evidence claims — create", () => {
  it("offers only the pinned contract's signals and the aggregate entries, with the metric names of the chosen entry", async () => {
    renderSection();
    await openCreate();
    expect(mockContract).toHaveBeenCalledWith("campaign-1", "EXP-1");
    expect(mockMetrics).toHaveBeenCalledWith("campaign-1");
    expect(screen.getByRole("option", { name: "Tasa de clics (RSG-1)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /MET-0 · .* existe una entrada posterior en la misma agrupación/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Métrica")).toBeDisabled(); // no entry chosen yet
    await userEvent.selectOptions(
      screen.getByLabelText("Entrada de métricas"),
      screen.getByRole("option", { name: /MET-1 · Instagram/ }),
    );
    const metric = screen.getByLabelText("Métrica");
    expect(within(metric).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Elige una métrica",
      "clicks",
      "impressions",
    ]);
  });

  it("rejects an incomplete selection without calling the API", async () => {
    renderSection();
    await openCreate();
    await userEvent.click(screen.getByText("Confirmar afirmación"));
    expect(screen.getByRole("alert")).toHaveTextContent("Elige la señal requerida.");
    await userEvent.selectOptions(screen.getByLabelText("Señal requerida"), "RSG-1");
    await userEvent.click(screen.getByText("Confirmar afirmación"));
    expect(screen.getByRole("alert")).toHaveTextContent("Elige la entrada de métricas.");
    await userEvent.selectOptions(
      screen.getByLabelText("Entrada de métricas"),
      screen.getByRole("option", { name: /MET-1 · Instagram/ }),
    );
    await userEvent.click(screen.getByText("Confirmar afirmación"));
    expect(screen.getByRole("alert")).toHaveTextContent("Elige la métrica.");
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("sends exactly the four frozen inputs, then refreshes the list and closes the form", async () => {
    mockCreate.mockResolvedValue(claim());
    renderSection();
    await openCreate();
    await fillCreate();
    mockList.mockResolvedValue(listing([claim()]));
    await userEvent.click(screen.getByText("Confirmar afirmación"));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith("campaign-1", "EXP-1", "EXS-1", {
      client_request_id: "uuid-1",
      required_signal_id: "RSG-1",
      metric_entry_id: "MET-1",
      metric_name: "clicks",
    });
    expect(Object.keys(mockCreate.mock.calls[0][3]).sort()).toEqual(
      ["client_request_id", "metric_entry_id", "metric_name", "required_signal_id"],
    );
    expect(await screen.findByText("ECL-1")).toBeInTheDocument();
    expect(screen.queryByText("Confirmar afirmación")).not.toBeInTheDocument();
  });

  it("keeps the client_request_id across a retry of the same material and rotates it after success", async () => {
    mockCreate
      .mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR", "offline"))
      .mockResolvedValueOnce(claim())
      .mockResolvedValueOnce(claim({ id: "ECL-2" }));
    renderSection();
    await openCreate();
    await fillCreate();
    await userEvent.click(screen.getByText("Confirmar afirmación"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No pudimos conectar con el servidor/);
    await userEvent.click(screen.getByText("Confirmar afirmación"));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(2));
    expect(mockCreate.mock.calls[0][3].client_request_id).toBe(mockCreate.mock.calls[1][3].client_request_id); // replayable
    await openCreateFromList();
    await fillCreate();
    await userEvent.click(screen.getByText("Confirmar afirmación"));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(3));
    expect(mockCreate.mock.calls[2][3].client_request_id).not.toBe(mockCreate.mock.calls[1][3].client_request_id);
  });

  it("rotates the key on an idempotency conflict and translates every frozen error", async () => {
    const cases: Array<[string, number, RegExp]> = [
      ["EVIDENCE_CLAIM_METRIC_NOT_IN_ENTRY", 422, /La métrica elegida no existe en esa entrada/],
      ["EVIDENCE_CLAIM_SIGNAL_NOT_IN_PINNED_CONTRACT", 422, /no pertenece al contrato de medición fijado por este inicio/],
      ["IDEMPOTENCY_KEY_CONFLICT", 409, /no coincide con un envío anterior/],
      ["FORBIDDEN", 403, /No encontramos esta campaña, o no tienes acceso a ella/],
    ];
    for (const [code, status, message] of cases) {
      vi.clearAllMocks();
      mockCreate.mockReset();
      uuidCounter = 0;
      mockList.mockResolvedValue(listing([]));
      mockContract.mockResolvedValue(CONTRACT);
      mockMetrics.mockResolvedValue(METRICS);
      mockCreate.mockRejectedValueOnce(new ApiError(status, code, "x")).mockResolvedValueOnce(claim());
      const view = renderSection();
      await openCreate();
      await fillCreate();
      await userEvent.click(screen.getByText("Confirmar afirmación"));
      expect(await screen.findByRole("alert")).toHaveTextContent(message);
      if (code === "IDEMPOTENCY_KEY_CONFLICT") {
        await userEvent.click(screen.getByText("Confirmar afirmación"));
        await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(2));
        expect(mockCreate.mock.calls[1][3].client_request_id).not.toBe(mockCreate.mock.calls[0][3].client_request_id);
      }
      view.unmount();
    }
  });

  it("refreshes the list and closes the form when a claim of the same material is already active", async () => {
    mockCreate.mockRejectedValue(new ApiError(409, "EVIDENCE_CLAIM_ALREADY_ACTIVE", "dup"));
    renderSection();
    await openCreate();
    await fillCreate();
    mockList.mockResolvedValue(listing([claim()]));
    await userEvent.click(screen.getByText("Confirmar afirmación"));
    expect(await screen.findByText("ECL-1")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/Ya existe una afirmación activa para este dato y esta señal/);
    expect(screen.queryByText("Confirmar afirmación")).not.toBeInTheDocument();
  });

  it("allows claiming an entry that already has a later correction (historical) and says so in the picker", async () => {
    mockCreate.mockResolvedValue(claim({ later_correction_exists: true }));
    renderSection();
    await openCreate();
    await fillCreate(/MET-0 · .* existe una entrada posterior/, "clicks");
    await userEvent.click(screen.getByText("Confirmar afirmación"));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    expect(mockCreate.mock.calls[0][3].metric_entry_id).toBe("MET-0");
  });

  it("translates a failure while loading the pickers", async () => {
    mockMetrics.mockRejectedValue(new ApiError(0, "NETWORK_ERROR", "offline"));
    renderSection();
    await userEvent.click(await screen.findByText("Registrar afirmación de evidencia"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No pudimos conectar con el servidor/);
  });
});

describe("Evidence claims — dispose", () => {
  it("explains what disposal means and does not mean before asking for a reason", async () => {
    mockList.mockResolvedValue(listing([claim()]));
    renderSection();
    await userEvent.click(await screen.findByText("Descartar afirmación ECL-1"));
    const panel = screen.getByText(/Descartar es definitivo/).closest(".panel") as HTMLElement;
    expect(panel).toHaveTextContent(/no se puede deshacer/);
    expect(panel).toHaveTextContent(/ya no quiere que esta afirmación se considere en el futuro/);
    expect(panel).toHaveTextContent(/no significa que la afirmación no ocurriera/);
    expect(panel).toHaveTextContent(/falsos o inválidos/);
    expect(panel).toHaveTextContent(/sigue visible/);
    expect(within(panel).getAllByRole("button").map((b) => b.textContent)).toEqual(["Confirmar descarte", "Cancelar"]);
  });

  it("requires a non-blank reason within the bound and does not call the API otherwise", async () => {
    mockList.mockResolvedValue(listing([claim()]));
    renderSection();
    await userEvent.click(await screen.findByText("Descartar afirmación ECL-1"));
    await userEvent.click(screen.getByText("Confirmar descarte"));
    expect(screen.getByRole("alert")).toHaveTextContent("Debes indicar el motivo para descartar la afirmación.");
    await userEvent.type(screen.getByLabelText("Motivo para descartar"), "   ");
    await userEvent.click(screen.getByText("Confirmar descarte"));
    expect(screen.getByRole("alert")).toHaveTextContent("Debes indicar el motivo");
    await userEvent.click(screen.getByLabelText("Motivo para descartar"));
    await userEvent.paste("x".repeat(1001));
    await userEvent.click(screen.getByText("Confirmar descarte"));
    expect(screen.getByRole("alert")).toHaveTextContent(/no puede superar 1000 caracteres/);
    expect(mockDispose).not.toHaveBeenCalled();
  });

  it("sends the explicit claim id and the trimmed reason (no idempotency key), then shows the disposed claim", async () => {
    mockList.mockResolvedValue(listing([claim()]));
    mockDispose.mockResolvedValue(claim({ is_disposed: true }));
    renderSection();
    await userEvent.click(await screen.findByText("Descartar afirmación ECL-1"));
    await userEvent.type(screen.getByLabelText("Motivo para descartar"), "  Métrica equivocada.  ");
    mockList.mockResolvedValue(
      listing([claim({ is_disposed: true, disposed_at: "2026-03-03T00:00:00Z", disposed_by: "USR-A", disposal_reason: "Métrica equivocada." })]),
    );
    await userEvent.click(screen.getByText("Confirmar descarte"));
    await waitFor(() => expect(mockDispose).toHaveBeenCalledTimes(1));
    expect(mockDispose).toHaveBeenCalledWith("campaign-1", "EXP-1", "EXS-1", "ECL-1", { reason: "Métrica equivocada." });
    const item = (await screen.findByText("ECL-1")).closest("li") as HTMLElement;
    await waitFor(() => expect(item).toHaveTextContent(/descartada/));
    expect(screen.queryByText("Descartar afirmación ECL-1")).not.toBeInTheDocument();
  });

  it("refreshes when the claim was already disposed and translates the error", async () => {
    mockList.mockResolvedValue(listing([claim()]));
    mockDispose.mockRejectedValue(new ApiError(409, "EVIDENCE_CLAIM_ALREADY_DISPOSED", "gone"));
    renderSection();
    await userEvent.click(await screen.findByText("Descartar afirmación ECL-1"));
    await userEvent.type(screen.getByLabelText("Motivo para descartar"), "Otra razón.");
    mockList.mockResolvedValue(listing([claim({ is_disposed: true, disposal_reason: "Otra persona.", disposed_at: "2026-03-03T00:00:00Z" })]));
    await userEvent.click(screen.getByText("Confirmar descarte"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Esta afirmación ya fue descartada/);
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("Confirmar descarte")).not.toBeInTheDocument();
  });

  it("keeps the panel open with a translated error on a non-terminal failure", async () => {
    mockList.mockResolvedValue(listing([claim()]));
    mockDispose.mockRejectedValue(new ApiError(0, "NETWORK_ERROR", "offline"));
    renderSection();
    await userEvent.click(await screen.findByText("Descartar afirmación ECL-1"));
    await userEvent.type(screen.getByLabelText("Motivo para descartar"), "Motivo.");
    await userEvent.click(screen.getByText("Confirmar descarte"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No pudimos conectar con el servidor/);
    expect(screen.getByText("Confirmar descarte")).toBeInTheDocument();
  });
});

describe("Evidence claims — Pre-Execution Measurement Declaration suggestion", () => {
  const BOUND_CONTRACT = {
    ...CONTRACT,
    declaration_level: "DESCRIPTIVE" as const,
    declaration_semantics_version: 1,
    signals: [
      {
        ...CONTRACT.signals[0],
        bound_metric_name: "clicks",
        channel_binding: "EXACT" as const,
        bound_channel: "Instagram",
        min_data_points: 2,
      },
    ],
  };

  it("suggests the declared metric and channel for a bound signal without claiming eligibility", async () => {
    mockContract.mockResolvedValue(BOUND_CONTRACT);
    renderSection();
    await openCreate();
    expect(screen.queryByTestId("declared-binding-hint")).not.toBeInTheDocument(); // no signal chosen yet
    await userEvent.selectOptions(screen.getByLabelText("Señal requerida"), "RSG-1");
    const hint = screen.getByTestId("declared-binding-hint");
    expect(hint).toHaveTextContent(/métrica «clicks» en el canal exacto «Instagram»/);
    expect(hint).toHaveTextContent(/estructuralmente compatibles/);
    expect(hint).toHaveTextContent(/no significa que el dato sea elegible, válido ni suficiente/);
    await userEvent.selectOptions(
      screen.getByLabelText("Entrada de métricas"),
      screen.getByRole("option", { name: /MET-1 · Instagram/ }),
    );
    expect(screen.getByRole("option", { name: "clicks (declarada)" })).toBeInTheDocument();
  });

  it("says any channel for an ANY binding and shows no hint for a legacy signal", async () => {
    mockContract.mockResolvedValue({
      ...BOUND_CONTRACT,
      signals: [{ ...BOUND_CONTRACT.signals[0], channel_binding: "ANY" as const, bound_channel: null }],
    });
    const { unmount } = renderSection();
    await openCreate();
    await userEvent.selectOptions(screen.getByLabelText("Señal requerida"), "RSG-1");
    expect(screen.getByTestId("declared-binding-hint")).toHaveTextContent(/métrica «clicks» en cualquier canal/);
    unmount();

    mockContract.mockResolvedValue(CONTRACT);
    renderSection();
    await openCreate();
    await userEvent.selectOptions(screen.getByLabelText("Señal requerida"), "RSG-1");
    expect(screen.queryByTestId("declared-binding-hint")).not.toBeInTheDocument();
  });

  it("translates the two binding rejections", async () => {
    for (const [code, message] of [
      ["EVIDENCE_CLAIM_METRIC_NOT_BOUND", /no es la que esta señal declara/],
      ["EVIDENCE_CLAIM_CHANNEL_NOT_BOUND", /no es el canal exacto que esta señal declara/],
    ] as const) {
      vi.clearAllMocks();
      mockCreate.mockReset();
      mockList.mockResolvedValue(listing([]));
      mockContract.mockResolvedValue(BOUND_CONTRACT);
      mockMetrics.mockResolvedValue(METRICS);
      mockCreate.mockRejectedValueOnce(new ApiError(422, code, "x"));
      const view = renderSection();
      await openCreate();
      await fillCreate();
      await userEvent.click(screen.getByText("Confirmar afirmación"));
      expect(await screen.findByRole("alert")).toHaveTextContent(message);
      view.unmount();
    }
  });
});
