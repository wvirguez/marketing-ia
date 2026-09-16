import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContentDistributionEvidenceSection } from "@/components/campaigns/detail/content-distribution-evidence-section";
import { ApiError } from "@/lib/api/client";
import type { DistributionEvidencePublic, DistributionEvidenceSummaryPublic } from "@/types/content";

vi.mock("@/lib/api/content", () => ({
  getDistributionEvidenceSummary: vi.fn(),
  listDistributionEvidence: vi.fn(),
  recordDistributionEvidence: vi.fn(),
  recordDistributionEvidenceCorrection: vi.fn(),
}));

import {
  getDistributionEvidenceSummary,
  listDistributionEvidence,
  recordDistributionEvidence,
  recordDistributionEvidenceCorrection,
} from "@/lib/api/content";

const mockList = vi.mocked(listDistributionEvidence);
const mockCreate = vi.mocked(recordDistributionEvidence);
const mockCorrect = vi.mocked(recordDistributionEvidenceCorrection);
const mockSummary = vi.mocked(getDistributionEvidenceSummary);

function makeSummary(overrides: Partial<DistributionEvidenceSummaryPublic> = {}): DistributionEvidenceSummaryPublic {
  return {
    content_piece_id: "CNT-1",
    distribution_id: "DST-1",
    content_version_id: "CNV-1",
    channel: "Instagram",
    metrics: [],
    ...overrides,
  };
}

let uuidCounter = 0;

beforeEach(() => {
  vi.clearAllMocks();
  uuidCounter = 0;
  vi.spyOn(crypto, "randomUUID").mockImplementation(() => `uuid-${++uuidCounter}` as ReturnType<typeof crypto.randomUUID>);
  // Every pre-existing test in this file renders with `distributed`, which
  // now also triggers an independent summary fetch (MVP-21) — default it
  // to a harmless empty summary so those tests need no changes; tests that
  // care about the summary panel itself override this explicitly.
  mockSummary.mockResolvedValue(makeSummary());
});

function makeEvidence(overrides: Partial<DistributionEvidencePublic> = {}): DistributionEvidencePublic {
  return {
    id: "DME-1",
    distribution_id: "DST-1",
    content_piece_id: "CNT-1",
    metric_entry_id: "MET-1",
    evidence_scope: "DISTRIBUTION_SPECIFIC",
    values: { reach: "500.0000" },
    period_start: "2026-01-05",
    period_end: "2026-01-10",
    channel: "Instagram",
    source: "MANUAL",
    source_reference: null,
    reported_by: "USR-1",
    reported_at: "2026-01-11T00:00:00Z",
    is_current: true,
    supersedes_evidence_id: null,
    correction_reason: null,
    ...overrides,
  };
}

async function fillMinimalCreateForm(user: ReturnType<typeof userEvent.setup>) {
  fireEvent.change(screen.getByLabelText("Inicio del período"), { target: { value: "2026-01-05" } });
  fireEvent.change(screen.getByLabelText("Fin del período"), { target: { value: "2026-01-10" } });
  await user.type(screen.getByLabelText("Nombre de la métrica"), "reach");
  await user.type(screen.getByLabelText("Valor"), "500");
}

describe("ContentDistributionEvidenceSection", () => {
  it("renders nothing before the piece/distribution is DISTRIBUTED", () => {
    const { container } = render(
      <ContentDistributionEvidenceSection campaignId="CMP-1" contentId="CNT-1" distributed={false} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(mockList).not.toHaveBeenCalled();
  });

  it("renders the section and fetches evidence once DISTRIBUTED", async () => {
    mockList.mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 });
    render(<ContentDistributionEvidenceSection campaignId="CMP-1" contentId="CNT-1" distributed />);
    expect(await screen.findByText("Métricas reportadas para esta distribución")).toBeInTheDocument();
    // Appears twice: once for the raw history section, once for the
    // MVP-21 summary sub-panel — both use the identical frozen qualifier.
    expect(screen.getAllByText("Registro manual; sin atribución causal.")).toHaveLength(2);
    expect(mockList).toHaveBeenCalledWith("CMP-1", "CNT-1");
    expect(await screen.findByText("Aún no hay métricas reportadas para esta distribución.")).toBeInTheDocument();
  });

  it("never uses causal or attribution wording anywhere in the section", async () => {
    mockList.mockResolvedValue({ items: [makeEvidence()], limit: 20, offset: 0, total: 1 });
    render(<ContentDistributionEvidenceSection campaignId="CMP-1" contentId="CNT-1" distributed />);
    await screen.findByText("Métricas reportadas para esta distribución");
    const text = document.body.textContent?.toLowerCase() ?? "";
    for (const forbidden of ["atribuid", "generad", "verificad", "confirmad por la plataforma"]) {
      expect(text).not.toContain(forbidden);
    }
  });

  it("shows the create form, validates it, and reloads the list on success", async () => {
    mockList
      .mockResolvedValueOnce({ items: [], limit: 20, offset: 0, total: 0 })
      .mockResolvedValueOnce({ items: [makeEvidence()], limit: 20, offset: 0, total: 1 });
    mockCreate.mockResolvedValue(makeEvidence());
    const user = userEvent.setup();
    render(<ContentDistributionEvidenceSection campaignId="CMP-1" contentId="CNT-1" distributed />);
    await screen.findByText("Métricas reportadas para esta distribución");

    await user.click(screen.getByRole("button", { name: "Agregar métricas reportadas" }));
    // Submit empty — validation blocks the call.
    await user.click(screen.getByRole("button", { name: "Agregar métricas reportadas" }));
    expect(await screen.findByText("Ingresa la fecha de inicio.")).toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();

    await fillMinimalCreateForm(user);
    await user.click(screen.getByRole("button", { name: "Agregar métricas reportadas" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith("CMP-1", "CNT-1", {
      period_start: "2026-01-05",
      period_end: "2026-01-10",
      values: { reach: "500" },
      client_request_id: "uuid-1",
      source_reference: null,
    });
    expect(await screen.findByText("500.0000")).toBeInTheDocument();
    expect(mockList).toHaveBeenCalledTimes(2);
  });

  it("shows a non-leaky server error and leaves the last confirmed list untouched", async () => {
    mockList.mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 });
    mockCreate.mockRejectedValue(new ApiError(409, "INVALID_LIFECYCLE_TRANSITION", "internal detail should not leak"));
    const user = userEvent.setup();
    render(<ContentDistributionEvidenceSection campaignId="CMP-1" contentId="CNT-1" distributed />);
    await screen.findByText("Métricas reportadas para esta distribución");

    await user.click(screen.getByRole("button", { name: "Agregar métricas reportadas" }));
    await fillMinimalCreateForm(user);
    await user.click(screen.getByRole("button", { name: "Agregar métricas reportadas" }));

    expect(await screen.findByText("No pudimos completar esta acción. Intenta de nuevo.")).toBeInTheDocument();
    expect(screen.queryByText(/internal detail/i)).not.toBeInTheDocument();
    expect(mockList).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Aún no hay métricas reportadas para esta distribución.")).toBeInTheDocument();
  });

  it("is single-flight: a second click while a create is pending issues no second request", async () => {
    let resolveCreate: (value: DistributionEvidencePublic) => void = () => {};
    mockList.mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 });
    mockCreate.mockImplementation(() => new Promise((resolve) => { resolveCreate = resolve; }));
    const user = userEvent.setup();
    render(<ContentDistributionEvidenceSection campaignId="CMP-1" contentId="CNT-1" distributed />);
    await screen.findByText("Métricas reportadas para esta distribución");

    await user.click(screen.getByRole("button", { name: "Agregar métricas reportadas" }));
    await fillMinimalCreateForm(user);
    const submit = screen.getByRole("button", { name: "Agregar métricas reportadas" });
    await user.click(submit);
    expect(screen.getByRole("button", { name: "Guardando…" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Guardando…" }));
    expect(mockCreate).toHaveBeenCalledTimes(1);
    resolveCreate(makeEvidence());
  });

  it("shows Corregir only on the current row and requires a correction reason", async () => {
    mockList.mockResolvedValue({
      items: [makeEvidence({ id: "DME-1", is_current: false }), makeEvidence({ id: "DME-2", is_current: true })],
      limit: 20, offset: 0, total: 2,
    });
    const user = userEvent.setup();
    render(<ContentDistributionEvidenceSection campaignId="CMP-1" contentId="CNT-1" distributed />);
    await screen.findByText("Métricas reportadas para esta distribución");

    const correctButtons = screen.getAllByRole("button", { name: "Corregir" });
    expect(correctButtons).toHaveLength(1);

    await user.click(correctButtons[0]);
    expect(screen.getByRole("button", { name: "Guardar corrección" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Guardar corrección" }));
    expect(await screen.findByText("Explica por qué corriges este registro.")).toBeInTheDocument();
    expect(mockCorrect).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText("Motivo de la corrección"), "Corrección de captura.");
    await user.click(screen.getByRole("button", { name: "Guardar corrección" }));
    await waitFor(() => expect(mockCorrect).toHaveBeenCalledTimes(1));
    expect(mockCorrect).toHaveBeenCalledWith(
      "CMP-1", "CNT-1", "DME-2",
      expect.objectContaining({ correction_reason: "Corrección de captura." }),
    );
  });

  describe("evidence summary (MVP-21)", () => {
    it("fetches and renders the server-computed summary independently of the raw history", async () => {
      mockList.mockResolvedValue({ items: [makeEvidence()], limit: 20, offset: 0, total: 1 });
      mockSummary.mockResolvedValue(
        makeSummary({
          metrics: [
            {
              metric_name: "reach", report_count: 2, latest_value: "750.0000",
              latest_period_start: "2026-01-05", latest_period_end: "2026-01-10", latest_reported_at: "2026-01-11T00:00:00Z",
              earliest_value: "500.0000", earliest_reported_at: "2026-01-01T00:00:00Z",
            },
          ],
        }),
      );
      render(<ContentDistributionEvidenceSection campaignId="CMP-1" contentId="CNT-1" distributed />);

      const summaryHeading = await screen.findByText("Resumen de evidencia reportada");
      const summaryPanel = summaryHeading.closest(".panel") as HTMLElement;
      expect(mockSummary).toHaveBeenCalledWith("CMP-1", "CNT-1");
      expect(within(summaryPanel).getByText(/reach/)).toBeInTheDocument();
      expect(within(summaryPanel).getByText(/750\.0000/)).toBeInTheDocument();
      expect(within(summaryPanel).getByText(/2 reporte\(s\)/)).toBeInTheDocument();
      // The component never recomputes report_count/latest from the raw
      // history array it also holds — it renders exactly what the summary
      // endpoint returned, and report_count (2) differs from the raw
      // history's own length (1) precisely to prove that.
    });

    it("renders the exact non-implying-zero-performance empty copy", async () => {
      mockList.mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 });
      mockSummary.mockResolvedValue(makeSummary({ metrics: [] }));
      render(<ContentDistributionEvidenceSection campaignId="CMP-1" contentId="CNT-1" distributed />);
      expect(await screen.findByText("Aún no hay evidencia reportada para resumir.")).toBeInTheDocument();
    });

    it("isolates a summary-fetch failure from the raw history and the rest of the section", async () => {
      mockList.mockResolvedValue({ items: [makeEvidence()], limit: 20, offset: 0, total: 1 });
      mockSummary.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
      render(<ContentDistributionEvidenceSection campaignId="CMP-1" contentId="CNT-1" distributed />);

      expect(await screen.findByText("Ocurrió un error inesperado. Intenta de nuevo en unos minutos.")).toBeInTheDocument();
      // The raw history — fetched independently — still renders normally.
      expect(await screen.findByText("500.0000")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Agregar métricas reportadas" })).toBeInTheDocument();
    });

    it("retrying a failed summary fetch calls the summary endpoint again, not the raw list", async () => {
      mockList.mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 });
      mockSummary.mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom")).mockResolvedValueOnce(makeSummary());
      const user = userEvent.setup();
      render(<ContentDistributionEvidenceSection campaignId="CMP-1" contentId="CNT-1" distributed />);
      await screen.findByText("Ocurrió un error inesperado. Intenta de nuevo en unos minutos.");

      await user.click(screen.getByRole("button", { name: "Reintentar" }));
      await screen.findByText("Aún no hay evidencia reportada para resumir.");
      expect(mockSummary).toHaveBeenCalledTimes(2);
      expect(mockList).toHaveBeenCalledTimes(1);
    });

    it("refetches the summary after a successful create, alongside the raw history", async () => {
      mockList
        .mockResolvedValueOnce({ items: [], limit: 20, offset: 0, total: 0 })
        .mockResolvedValueOnce({ items: [makeEvidence()], limit: 20, offset: 0, total: 1 });
      mockCreate.mockResolvedValue(makeEvidence());
      const user = userEvent.setup();
      render(<ContentDistributionEvidenceSection campaignId="CMP-1" contentId="CNT-1" distributed />);
      await screen.findByText("Métricas reportadas para esta distribución");
      expect(mockSummary).toHaveBeenCalledTimes(1);

      await user.click(screen.getByRole("button", { name: "Agregar métricas reportadas" }));
      await fillMinimalCreateForm(user);
      await user.click(screen.getByRole("button", { name: "Agregar métricas reportadas" }));

      await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(mockSummary).toHaveBeenCalledTimes(2));
    });

    it("never uses causal or comparative wording in the summary panel", async () => {
      mockSummary.mockResolvedValue(
        makeSummary({
          metrics: [
            {
              metric_name: "reach", report_count: 1, latest_value: "500.0000",
              latest_period_start: "2026-01-05", latest_period_end: "2026-01-10", latest_reported_at: "2026-01-11T00:00:00Z",
              earliest_value: "500.0000", earliest_reported_at: "2026-01-11T00:00:00Z",
            },
          ],
        }),
      );
      render(<ContentDistributionEvidenceSection campaignId="CMP-1" contentId="CNT-1" distributed />);
      await screen.findByText("Resumen de evidencia reportada");
      const text = document.body.textContent?.toLowerCase() ?? "";
      for (const forbidden of ["generad", "caused", "causad", "atribuid", "mejor", "peor", "mejoró", "empeoró"]) {
        expect(text).not.toContain(forbidden);
      }
    });
  });
});
