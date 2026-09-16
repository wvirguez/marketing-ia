import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CampaignDistributionEvidenceRollupPanel } from "@/components/campaigns/detail/campaign-distribution-evidence-rollup-panel";
import { ApiError } from "@/lib/api/client";
import type { CampaignDistributionEvidenceRollupPublic, CampaignMetricSummaryItem } from "@/types/measurement";

vi.mock("@/lib/api/measurement", () => ({
  getCampaignDistributionEvidenceRollup: vi.fn(),
}));

import { getCampaignDistributionEvidenceRollup } from "@/lib/api/measurement";

const mockGetRollup = vi.mocked(getCampaignDistributionEvidenceRollup);

beforeEach(() => {
  vi.clearAllMocks();
});

function makeObservation(overrides: Partial<CampaignMetricSummaryItem["latest"]> = {}) {
  return {
    value: "750.0000",
    period_start: "2026-01-05",
    period_end: "2026-01-10",
    reported_at: "2026-01-11T00:00:00Z",
    content_piece_id: "CNT-1",
    distribution_id: "DST-1",
    content_version_id: "CNV-1",
    channel: "Instagram",
    ...overrides,
  };
}

function makeRollup(overrides: Partial<CampaignDistributionEvidenceRollupPublic> = {}): CampaignDistributionEvidenceRollupPublic {
  return {
    campaign_id: "CMP-1",
    metrics: [],
    ...overrides,
  };
}

describe("CampaignDistributionEvidenceRollupPanel", () => {
  it("does not fetch while inactive, fetches once the tab becomes active", async () => {
    mockGetRollup.mockResolvedValue(makeRollup());
    const { rerender } = render(
      <CampaignDistributionEvidenceRollupPanel campaignId="CMP-1" active={false} refreshToken={0} />,
    );
    expect(mockGetRollup).not.toHaveBeenCalled();

    rerender(<CampaignDistributionEvidenceRollupPanel campaignId="CMP-1" active refreshToken={0} />);
    expect(await screen.findByText("Resumen de evidencia reportada de la campaña")).toBeInTheDocument();
    expect(mockGetRollup).toHaveBeenCalledWith("CMP-1");
    expect(mockGetRollup).toHaveBeenCalledTimes(1);
  });

  it("renders the exact non-causal qualifier and heading", async () => {
    mockGetRollup.mockResolvedValue(makeRollup());
    render(<CampaignDistributionEvidenceRollupPanel campaignId="CMP-1" active refreshToken={0} />);
    expect(await screen.findByText("Resumen de evidencia reportada de la campaña")).toBeInTheDocument();
    expect(
      screen.getByText("Registro descriptivo de evidencia reportada en todas las distribuciones; sin atribución causal."),
    ).toBeInTheDocument();
  });

  it("renders the exact empty copy without implying a measured zero", async () => {
    mockGetRollup.mockResolvedValue(makeRollup({ metrics: [] }));
    render(<CampaignDistributionEvidenceRollupPanel campaignId="CMP-1" active refreshToken={0} />);
    expect(await screen.findByText("Aún no hay evidencia reportada para esta campaña.")).toBeInTheDocument();
  });

  it("renders the server-computed report_count, latest, and earliest exactly as returned — no client recomputation", async () => {
    mockGetRollup.mockResolvedValue(
      makeRollup({
        metrics: [
          {
            metric_name: "reach",
            report_count: 5,
            latest: makeObservation({ value: "750.0000" }),
            earliest: makeObservation({ value: "500.0000", period_start: "2026-01-01", period_end: "2026-01-02" }),
          },
        ],
      }),
    );
    render(<CampaignDistributionEvidenceRollupPanel campaignId="CMP-1" active refreshToken={0} />);
    expect(await screen.findByText("reach")).toBeInTheDocument();
    // report_count (5) intentionally differs from any derivable "number of
    // observations shown" (2: one latest, one earliest) — proving the
    // component renders the server's own count, not something it computed.
    expect(screen.getByText("5 reporte(s) actual(es) en la campaña.")).toBeInTheDocument();
    expect(screen.getByText(/750\.0000/)).toBeInTheDocument();
    expect(screen.getByText(/500\.0000/)).toBeInTheDocument();
  });

  it("renders metrics in the exact server order, never re-sorting client-side", async () => {
    mockGetRollup.mockResolvedValue(
      makeRollup({
        metrics: [
          { metric_name: "zzz_last", report_count: 1, latest: makeObservation(), earliest: makeObservation() },
          { metric_name: "aaa_first", report_count: 1, latest: makeObservation(), earliest: makeObservation() },
        ],
      }),
    );
    render(<CampaignDistributionEvidenceRollupPanel campaignId="CMP-1" active refreshToken={0} />);
    await screen.findByText("zzz_last");
    const headings = screen.getAllByRole("heading", { level: 3 }).map((el) => el.textContent);
    const metricHeadings = headings.filter((h) => h === "zzz_last" || h === "aaa_first");
    expect(metricHeadings).toEqual(["zzz_last", "aaa_first"]);
  });

  it("isolates a fetch failure locally and supports retry", async () => {
    mockGetRollup.mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom")).mockResolvedValueOnce(makeRollup());
    const user = userEvent.setup();
    render(<CampaignDistributionEvidenceRollupPanel campaignId="CMP-1" active refreshToken={0} />);
    expect(await screen.findByText("Ocurrió un error inesperado. Intenta de nuevo en unos minutos.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("Aún no hay evidencia reportada para esta campaña.")).toBeInTheDocument();
    expect(mockGetRollup).toHaveBeenCalledTimes(2);
  });

  it("never uses causal or comparative wording anywhere in the panel", async () => {
    mockGetRollup.mockResolvedValue(
      makeRollup({
        metrics: [{ metric_name: "revenue", report_count: 2, latest: makeObservation(), earliest: makeObservation() }],
      }),
    );
    render(<CampaignDistributionEvidenceRollupPanel campaignId="CMP-1" active refreshToken={0} />);
    await screen.findByText("Resumen de evidencia reportada de la campaña");
    const text = document.body.textContent?.toLowerCase() ?? "";
    for (const forbidden of ["generad", "caused", "causad", "atribuid", "mejor", "peor", "ganador", "rendimiento"]) {
      expect(text).not.toContain(forbidden);
    }
  });

  it("refetches only when refreshToken changes while active", async () => {
    mockGetRollup.mockResolvedValue(makeRollup());
    const { rerender } = render(
      <CampaignDistributionEvidenceRollupPanel campaignId="CMP-1" active refreshToken={0} />,
    );
    await screen.findByText("Resumen de evidencia reportada de la campaña");
    expect(mockGetRollup).toHaveBeenCalledTimes(1);

    rerender(<CampaignDistributionEvidenceRollupPanel campaignId="CMP-1" active refreshToken={0} />);
    expect(mockGetRollup).toHaveBeenCalledTimes(1);

    rerender(<CampaignDistributionEvidenceRollupPanel campaignId="CMP-1" active refreshToken={1} />);
    expect(mockGetRollup).toHaveBeenCalledTimes(2);
  });
});
