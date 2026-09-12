import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContentAssetsSection } from "@/components/campaigns/detail/content-assets-section";
import { ContentDetailView } from "@/components/campaigns/detail/content-detail-view";
import { ApiError } from "@/lib/api/client";
import type { AssetPublic, AssetsForContentPieceResponse } from "@/types/assets";
import type { ContentPieceDetailResponse } from "@/types/content";

vi.mock("@/lib/api/assets", () => ({
  getAssetsForContent: vi.fn(),
}));
vi.mock("@/lib/api/content", () => ({
  getContentDetail: vi.fn(),
}));

import { getAssetsForContent } from "@/lib/api/assets";
import { getContentDetail } from "@/lib/api/content";

const mockGetAssetsForContent = vi.mocked(getAssetsForContent);
const mockGetContentDetail = vi.mocked(getContentDetail);

const EMPTY_COPY = "No hay creatividades registradas para esta pieza todavía.";
const NO_ASSETS_WITH_BRIEF_COPY = "No hay activos registrados para este brief todavía.";
const NO_VERSION_COPY = "Este activo no tiene una versión registrada disponible.";

beforeEach(() => {
  vi.clearAllMocks();
});

function makeAsset(overrides: Partial<AssetPublic> = {}): AssetPublic {
  return {
    id: "asset-1",
    kind: "image",
    status: "draft",
    current_version: {
      storage_reference: "s3://bucket/key.png",
      metadata: { width: 1080, height: 1350 },
      created_at: "2026-01-03T00:00:00Z",
    },
    ...overrides,
  };
}

function makeResponse(
  overrides: Partial<AssetsForContentPieceResponse> = {},
): AssetsForContentPieceResponse {
  return { creative_brief: null, assets: [], ...overrides };
}

describe("ContentAssetsSection", () => {
  it("renders the loading state while the request is unresolved", () => {
    mockGetAssetsForContent.mockImplementation(() => new Promise(() => {}));
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);

    expect(screen.getByRole("status")).toHaveTextContent("Cargando creatividades…");
  });

  it("renders truthful empty copy when there is no CreativeBrief and no Assets", async () => {
    mockGetAssetsForContent.mockResolvedValue(makeResponse());
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByText(EMPTY_COPY)).toBeInTheDocument();
    expect(screen.queryByText(/generando/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/procesando/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/pendiente/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/error al generar/i)).not.toBeInTheDocument();
  });

  it("renders the CreativeBrief without implying an Asset exists when assets is empty", async () => {
    mockGetAssetsForContent.mockResolvedValue(
      makeResponse({ creative_brief: { spec: { headline: "Oferta de lanzamiento" } } }),
    );
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByRole("heading", { name: "Brief creativo" })).toBeInTheDocument();
    expect(screen.getByText("headline")).toBeInTheDocument();
    expect(screen.getByText("Oferta de lanzamiento")).toBeInTheDocument();
    expect(screen.getByText(NO_ASSETS_WITH_BRIEF_COPY)).toBeInTheDocument();
    expect(screen.queryByText(EMPTY_COPY)).not.toBeInTheDocument();
  });

  it("renders an Asset with its current version, storage_reference as plain text (never a link)", async () => {
    mockGetAssetsForContent.mockResolvedValue(makeResponse({ assets: [makeAsset()] }));
    const { container } = render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByText("image")).toBeInTheDocument();
    expect(screen.getByText("draft")).toBeInTheDocument();
    expect(screen.getByText("s3://bucket/key.png")).toBeInTheDocument();
    expect(screen.getByText("width")).toBeInTheDocument();
    expect(screen.getByText("1080")).toBeInTheDocument();
    expect(container.querySelector("a")).toBeNull();
  });

  it("renders a neutral state for an Asset without a current version", async () => {
    mockGetAssetsForContent.mockResolvedValue(
      makeResponse({ assets: [makeAsset({ current_version: null })] }),
    );
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByText(NO_VERSION_COPY)).toBeInTheDocument();
  });

  it("shows a non-leaky error message and retries by issuing the GET again", async () => {
    mockGetAssetsForContent
      .mockRejectedValueOnce(new ApiError(403, "FORBIDDEN", "internal detail should not leak"))
      .mockResolvedValueOnce(makeResponse());

    const user = userEvent.setup();
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByText("No encontramos esta campaña, o no tienes acceso a ella.")).toBeInTheDocument();
    expect(screen.queryByText(/internal detail/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /reintentar/i }));

    expect(await screen.findByText(EMPTY_COPY)).toBeInTheDocument();
    expect(mockGetAssetsForContent).toHaveBeenCalledTimes(2);
    expect(mockGetAssetsForContent).toHaveBeenNthCalledWith(2, "campaign-1", "piece-1");
  });

  it("never implies content approval or distribution readiness from Asset presence or status", async () => {
    mockGetAssetsForContent.mockResolvedValue(
      makeResponse({
        creative_brief: { spec: { headline: "Oferta" } },
        assets: [makeAsset({ status: "ready" })],
      }),
    );
    const { container } = render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);

    await screen.findByText("ready");

    for (const forbidden of [
      /aprobado/i,
      /aprobada/i,
      /listo para distribuci/i,
      /distribuido/i,
      /publicado/i,
    ]) {
      expect(screen.queryByText(forbidden)).not.toBeInTheDocument();
    }
    // The raw backend status string is rendered as plain descriptive text,
    // never as the colored `.status` pill used for governance state.
    expect(container.querySelector(".status")).toBeNull();
  });
});

describe("ContentAssetsSection integration inside ContentDetailView", () => {
  it("passes the campaign and content public IDs through and renders the Assets section", async () => {
    const piece: ContentPieceDetailResponse["piece"] = {
      id: "piece-1",
      format: "Reel",
      objective: "Awareness",
      funnel_stage: "TOFU",
      cta: "Comprar ahora",
      channel: "Instagram",
      status: "DRAFT",
      archived_at: null,
      created_at: "2026-01-01T00:00:00Z",
    };
    mockGetContentDetail.mockResolvedValue({ piece, latest_version: null });
    mockGetAssetsForContent.mockResolvedValue({
      creative_brief: null,
      assets: [makeAsset()],
    });

    render(<ContentDetailView campaignId="campaign-42" contentId="piece-1" />);

    await screen.findByRole("heading", { name: "Reel" });
    expect(await screen.findByRole("heading", { name: "Creatividades" })).toBeInTheDocument();
    expect(mockGetAssetsForContent).toHaveBeenCalledWith("campaign-42", "piece-1");
    expect(screen.getByText("image")).toBeInTheDocument();
  });
});
