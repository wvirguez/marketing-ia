import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContentDetailView } from "@/components/campaigns/detail/content-detail-view";
import { ApiError } from "@/lib/api/client";
import type { ContentPieceDetailResponse, ContentPiecePublic, ContentVersionPublic } from "@/types/content";

vi.mock("@/lib/api/content", () => ({
  getContentDetail: vi.fn(),
}));

import { getContentDetail } from "@/lib/api/content";

const mockGetContentDetail = vi.mocked(getContentDetail);

const NO_VERSION_COPY = "Este contenido aún no tiene una versión generada.";

beforeEach(() => {
  vi.clearAllMocks();
});

function makePiece(overrides: Partial<ContentPiecePublic> = {}): ContentPiecePublic {
  return {
    id: "piece-1",
    format: "Reel",
    objective: "Awareness",
    funnel_stage: "TOFU",
    cta: "Comprar ahora",
    channel: "Instagram",
    status: "DRAFT",
    archived_at: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeVersion(payload: Record<string, unknown>): ContentVersionPublic {
  return { id: "version-1", payload, created_at: "2026-01-02T00:00:00Z" };
}

function makeDetail(
  piece: ContentPiecePublic,
  latest_version: ContentVersionPublic | null,
): ContentPieceDetailResponse {
  return { piece, latest_version };
}

describe("ContentDetailView", () => {
  it("renders the loading state while the request is unresolved", () => {
    mockGetContentDetail.mockImplementation(() => new Promise(() => {}));
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    expect(screen.getByRole("status")).toHaveTextContent("Cargando contenido…");
  });

  it("renders Piece-level fields from the piece itself", async () => {
    const piece = makePiece({
      format: "Carousel",
      objective: "Conversión",
      channel: "Facebook",
      funnel_stage: "BOFU",
      cta: "Reservar ahora",
    });
    mockGetContentDetail.mockResolvedValue(makeDetail(piece, null));
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByRole("heading", { name: "Carousel" })).toBeInTheDocument();
    expect(screen.getByText("Conversión")).toBeInTheDocument();
    expect(screen.getByText("Facebook")).toBeInTheDocument();
    expect(screen.getByText("BOFU")).toBeInTheDocument();
    expect(screen.getByText("Reservar ahora")).toBeInTheDocument();
  });

  it("renders the latest version's payload separately from the Piece's own metadata", async () => {
    const piece = makePiece({ objective: "Fidelización" });
    const version = makeVersion({ hook: "Descubre nuestra oferta", duration_seconds: 30 });
    mockGetContentDetail.mockResolvedValue(makeDetail(piece, version));
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    await screen.findByRole("heading", { name: piece.format });

    expect(screen.getByText("hook")).toBeInTheDocument();
    expect(screen.getByText("Descubre nuestra oferta")).toBeInTheDocument();
    expect(screen.getByText("duration_seconds")).toBeInTheDocument();
    expect(screen.getByText("30")).toBeInTheDocument();
    expect(screen.getByText(piece.objective)).toBeInTheDocument();
  });

  it("renders the no-version copy without crashing when latest_version is null", async () => {
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece(), null));
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByText(NO_VERSION_COPY)).toBeInTheDocument();
  });

  it("recursively renders scalars, null/empty values, arrays, and nested objects in the version payload", async () => {
    const payload = {
      title: "Título de prueba",
      count: 3,
      active: true,
      note: null,
      empty_text: "",
      tags: [],
      hashtags: ["#uno", "#dos"],
      meta: { author: "Equipo", length: 12 },
    };
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece(), makeVersion(payload)));
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    await screen.findByText(payload.title);

    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("true")).toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(3);
    expect(screen.getByText("#uno")).toBeInTheDocument();
    expect(screen.getByText("#dos")).toBeInTheDocument();
    expect(screen.getByText("author")).toBeInTheDocument();
    expect(screen.getByText("Equipo")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("renders an HTML-looking payload string as plain text, never as markup", async () => {
    const version = makeVersion({ script: "<strong>texto</strong>" });
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece(), version));
    const { container } = render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    await screen.findByText("<strong>texto</strong>");
    expect(container.querySelector("strong")).toBeNull();
  });

  it("shows a non-leaky error message and supports retry", async () => {
    mockGetContentDetail
      .mockRejectedValueOnce(new ApiError(404, "FORBIDDEN", "internal detail should not leak"))
      .mockResolvedValueOnce(makeDetail(makePiece(), null));

    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByText("No encontramos esta campaña, o no tienes acceso a ella.")).toBeInTheDocument();
    expect(screen.queryByText(/internal detail/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /reintentar/i }));

    expect(await screen.findByText(NO_VERSION_COPY)).toBeInTheDocument();
  });
});
