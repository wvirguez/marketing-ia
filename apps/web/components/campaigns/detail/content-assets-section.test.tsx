import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContentAssetsSection } from "@/components/campaigns/detail/content-assets-section";
import { ContentDetailView } from "@/components/campaigns/detail/content-detail-view";
import { ApiError } from "@/lib/api/client";
import type { AssetPublic, AssetsForContentPieceResponse } from "@/types/assets";
import type { ContentPieceDetailResponse } from "@/types/content";

vi.mock("@/lib/api/assets", () => ({
  getAssetsForContent: vi.fn(),
  createCreativeBrief: vi.fn(),
  createAsset: vi.fn(),
  createAssetVersion: vi.fn(),
}));
vi.mock("@/lib/api/content", () => ({
  getContentDetail: vi.fn(),
  markContentInProduction: vi.fn(),
  markContentProduced: vi.fn(),
  markContentReadyForReview: vi.fn(),
  requestContentApproval: vi.fn(),
  markContentApprovalUnderReview: vi.fn(),
  recordContentApprovalDecision: vi.fn(),
}));
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: vi.fn(),
}));

import { createAsset, createAssetVersion, createCreativeBrief, getAssetsForContent } from "@/lib/api/assets";
import { getContentDetail } from "@/lib/api/content";
import { useAuth } from "@/lib/auth/auth-context";

const mockGetAssetsForContent = vi.mocked(getAssetsForContent);
const mockCreateCreativeBrief = vi.mocked(createCreativeBrief);
const mockCreateAsset = vi.mocked(createAsset);
const mockCreateAssetVersion = vi.mocked(createAssetVersion);
const mockGetContentDetail = vi.mocked(getContentDetail);
const mockUseAuth = vi.mocked(useAuth);

const EMPTY_COPY = "No hay creatividades registradas para esta pieza todavía.";
const NO_ASSETS_WITH_BRIEF_COPY = "No hay activos registrados para este brief todavía.";
const NO_VERSION_COPY = "Este activo no tiene una versión registrada disponible.";
const CREATE_BRIEF_LABEL = "Crear brief creativo";
const CREATE_ASSET_LABEL = "Registrar activo";
const ADD_VERSION_LABEL = "Añadir versión";
const REFERENCE_LABEL = "Referencia externa (opcional)";

beforeEach(() => {
  vi.clearAllMocks();
  mockUseAuth.mockReturnValue({
    status: "authenticated",
    session: {
      user: { id: "USR-1", email: "real.user@impulso.test", display_name: "Real User", status: "ACTIVE", preferences: { locale: null, timezone: null } },
      workspace: { id: "WS-1", name: "Real Workspace", slug: "real-workspace" },
      membership: { role: "OWNER" },
    },
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn().mockResolvedValue(undefined),
  });
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

  // --- CreativeBrief creation (MVP-16B) -------------------------------------

  it("shows a create-CreativeBrief form in the empty state", async () => {
    mockGetAssetsForContent.mockResolvedValue(makeResponse());
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByText(EMPTY_COPY)).toBeInTheDocument();
    expect(screen.getByLabelText("Resumen del brief")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: CREATE_BRIEF_LABEL })).toBeInTheDocument();
  });

  it("calls createCreativeBrief with the typed summary and shows no optimistic brief before it resolves", async () => {
    mockGetAssetsForContent.mockResolvedValue(makeResponse());
    let resolveCreate: (value: AssetsForContentPieceResponse) => void = () => {};
    mockCreateCreativeBrief.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCreate = resolve;
        }),
    );

    const user = userEvent.setup();
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);
    await screen.findByText(EMPTY_COPY);

    await user.type(screen.getByLabelText("Resumen del brief"), "Tono divertido");
    await user.click(screen.getByRole("button", { name: CREATE_BRIEF_LABEL }));

    expect(mockCreateCreativeBrief).toHaveBeenCalledWith("campaign-1", "piece-1", { summary: "Tono divertido" });
    expect(screen.getByText(EMPTY_COPY)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Brief creativo" })).not.toBeInTheDocument();

    resolveCreate(makeResponse({ creative_brief: { spec: { summary: "Tono divertido" } } }));
    expect(await screen.findByRole("heading", { name: "Brief creativo" })).toBeInTheDocument();
  });

  it("preserves the empty state and permits retry when CreativeBrief creation fails", async () => {
    mockGetAssetsForContent.mockResolvedValue(makeResponse());
    mockCreateCreativeBrief.mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom"));

    const user = userEvent.setup();
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);
    await screen.findByText(EMPTY_COPY);

    await user.type(screen.getByLabelText("Resumen del brief"), "Tono divertido");
    await user.click(screen.getByRole("button", { name: CREATE_BRIEF_LABEL }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(EMPTY_COPY)).toBeInTheDocument();
  });

  // --- Asset creation (MVP-16B) ----------------------------------------------

  it("shows the Asset-creation form only once a CreativeBrief exists", async () => {
    mockGetAssetsForContent.mockResolvedValue(makeResponse());
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);

    await screen.findByText(EMPTY_COPY);
    expect(screen.queryByLabelText("Tipo de activo")).not.toBeInTheDocument();
  });

  it("renders the Asset-creation form with a truthfully labeled reference field", async () => {
    mockGetAssetsForContent.mockResolvedValue(makeResponse({ creative_brief: { spec: {} } }));
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);

    await screen.findByRole("heading", { name: "Brief creativo" });
    expect(screen.getByLabelText("Tipo de activo")).toBeInTheDocument();
    expect(screen.getByLabelText(REFERENCE_LABEL)).toBeInTheDocument();
    expect(screen.queryByText(/^URL$/i)).not.toBeInTheDocument();
  });

  it("replaces local state with the server response after a successful Asset creation", async () => {
    mockGetAssetsForContent.mockResolvedValue(makeResponse({ creative_brief: { spec: {} } }));
    mockCreateAsset.mockResolvedValue(makeResponse({ creative_brief: { spec: {} }, assets: [makeAsset()] }));

    const user = userEvent.setup();
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);
    await screen.findByRole("heading", { name: "Brief creativo" });

    await user.type(screen.getByLabelText("Tipo de activo"), "image");
    await user.type(screen.getByLabelText(REFERENCE_LABEL), "https://example.com/a.png");
    await user.click(screen.getByRole("button", { name: CREATE_ASSET_LABEL }));

    expect(mockCreateAsset).toHaveBeenCalledWith("campaign-1", "piece-1", {
      kind: "image",
      storage_reference: "https://example.com/a.png",
    });
    expect(await screen.findByText("s3://bucket/key.png")).toBeInTheDocument();
  });

  it("omits storage_reference entirely when left blank", async () => {
    mockGetAssetsForContent.mockResolvedValue(makeResponse({ creative_brief: { spec: {} } }));
    mockCreateAsset.mockResolvedValue(makeResponse({ creative_brief: { spec: {} }, assets: [makeAsset()] }));

    const user = userEvent.setup();
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);
    await screen.findByRole("heading", { name: "Brief creativo" });

    await user.type(screen.getByLabelText("Tipo de activo"), "video");
    await user.click(screen.getByRole("button", { name: CREATE_ASSET_LABEL }));

    expect(mockCreateAsset).toHaveBeenCalledWith("campaign-1", "piece-1", {
      kind: "video",
      storage_reference: undefined,
    });
  });

  it("preserves prior state and shows an error when Asset creation fails", async () => {
    mockGetAssetsForContent.mockResolvedValue(makeResponse({ creative_brief: { spec: {} } }));
    mockCreateAsset.mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom"));

    const user = userEvent.setup();
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);
    await screen.findByRole("heading", { name: "Brief creativo" });

    await user.type(screen.getByLabelText("Tipo de activo"), "image");
    await user.click(screen.getByRole("button", { name: CREATE_ASSET_LABEL }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(NO_ASSETS_WITH_BRIEF_COPY)).toBeInTheDocument();
  });

  // --- AssetVersion append (MVP-16B) -----------------------------------------

  it("replaces local state with the server response after a successful version append", async () => {
    mockGetAssetsForContent.mockResolvedValue(makeResponse({ assets: [makeAsset()] }));
    mockCreateAssetVersion.mockResolvedValue(
      makeResponse({
        assets: [makeAsset({ current_version: { storage_reference: "https://example.com/v2.png", metadata: {}, created_at: "2026-01-04T00:00:00Z" } })],
      }),
    );

    const user = userEvent.setup();
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);
    await screen.findByText("image");

    const input = screen.getByLabelText(`${REFERENCE_LABEL} para image`);
    await user.type(input, "https://example.com/v2.png");
    await user.click(screen.getByRole("button", { name: ADD_VERSION_LABEL }));

    expect(mockCreateAssetVersion).toHaveBeenCalledWith("campaign-1", "piece-1", "asset-1", {
      storage_reference: "https://example.com/v2.png",
    });
    expect(await screen.findByText("https://example.com/v2.png")).toBeInTheDocument();
  });

  it("preserves the confirmed version and shows an error when append fails", async () => {
    mockGetAssetsForContent.mockResolvedValue(makeResponse({ assets: [makeAsset()] }));
    mockCreateAssetVersion.mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom"));

    const user = userEvent.setup();
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);
    await screen.findByText("image");

    await user.click(screen.getByRole("button", { name: ADD_VERSION_LABEL }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("s3://bucket/key.png")).toBeInTheDocument();
  });

  // --- section-global single-flight lock (MVP-16B) ---------------------------

  it("disables every write control while one mutation is pending (section-global lock)", async () => {
    mockGetAssetsForContent.mockResolvedValue(makeResponse({ creative_brief: { spec: {} }, assets: [makeAsset()] }));
    let resolveCreate: (value: AssetsForContentPieceResponse) => void = () => {};
    mockCreateAsset.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCreate = resolve;
        }),
    );

    const user = userEvent.setup();
    render(<ContentAssetsSection campaignId="campaign-1" contentId="piece-1" />);
    await screen.findByText("image");

    await user.type(screen.getByLabelText("Tipo de activo"), "video");
    await user.click(screen.getByRole("button", { name: CREATE_ASSET_LABEL }));

    expect(screen.getByLabelText("Tipo de activo")).toBeDisabled();
    expect(screen.getByLabelText(REFERENCE_LABEL)).toBeDisabled();
    expect(screen.getByRole("button", { name: ADD_VERSION_LABEL })).toBeDisabled();
    expect(screen.getByLabelText(`${REFERENCE_LABEL} para image`)).toBeDisabled();

    resolveCreate(makeResponse({ creative_brief: { spec: {} }, assets: [makeAsset(), makeAsset({ id: "asset-2" })] }));
    await waitFor(() => expect(screen.getByLabelText("Tipo de activo")).not.toBeDisabled());
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
    mockGetContentDetail.mockResolvedValue({ piece, latest_version: null, latest_approval: null, distribution: null });
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
