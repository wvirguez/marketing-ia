import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContentDetailView } from "@/components/campaigns/detail/content-detail-view";
import { useAuth } from "@/lib/auth/auth-context";
import { ApiError } from "@/lib/api/client";
import type { AuthContextValue } from "@/lib/auth/auth-context";
import type { ContentApprovalPublic, ContentPieceDetailResponse, ContentPiecePublic, ContentVersionPublic } from "@/types/content";

vi.mock("@/lib/api/content", () => ({
  getContentDetail: vi.fn(),
  createContentRevisionVersion: vi.fn(),
  markContentInProduction: vi.fn(),
  markContentProduced: vi.fn(),
  markContentReadyForReview: vi.fn(),
  requestContentApproval: vi.fn(),
  markContentApprovalUnderReview: vi.fn(),
  recordContentApprovalDecision: vi.fn(),
  markReadyForDistribution: vi.fn(),
  recordDistributed: vi.fn(),
  listDistributionEvidence: vi.fn().mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 }),
  recordDistributionEvidence: vi.fn(),
  recordDistributionEvidenceCorrection: vi.fn(),
  getDistributionEvidenceSummary: vi.fn().mockResolvedValue({
    content_piece_id: "CNT-1", distribution_id: null, content_version_id: null, channel: null, metrics: [],
  }),
  associateTrackingRequirement: vi.fn(),
  dissociateTrackingRequirement: vi.fn(),
}));
vi.mock("@/lib/api/tracking", () => ({
  getTracking: vi.fn().mockResolvedValue({ plan: null }),
}));
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: vi.fn(),
}));

import {
  associateTrackingRequirement,
  createContentRevisionVersion,
  dissociateTrackingRequirement,
  getContentDetail,
  markContentApprovalUnderReview,
  markContentInProduction,
  markContentProduced,
  markContentReadyForReview,
  recordContentApprovalDecision,
  markReadyForDistribution,
  recordDistributed,
  requestContentApproval,
} from "@/lib/api/content";
import { getTracking } from "@/lib/api/tracking";

const mockGetContentDetail = vi.mocked(getContentDetail);
const mockCreateContentRevisionVersion = vi.mocked(createContentRevisionVersion);
const mockMarkContentInProduction = vi.mocked(markContentInProduction);
const mockMarkContentProduced = vi.mocked(markContentProduced);
const mockMarkContentReadyForReview = vi.mocked(markContentReadyForReview);
const mockRequestContentApproval = vi.mocked(requestContentApproval);
const mockMarkContentApprovalUnderReview = vi.mocked(markContentApprovalUnderReview);
const mockRecordContentApprovalDecision = vi.mocked(recordContentApprovalDecision);
const mockMarkReadyForDistribution = vi.mocked(markReadyForDistribution);
const mockRecordDistributed = vi.mocked(recordDistributed);
const mockAssociateTrackingRequirement = vi.mocked(associateTrackingRequirement);
const mockDissociateTrackingRequirement = vi.mocked(dissociateTrackingRequirement);
const mockGetTracking = vi.mocked(getTracking);
const mockUseAuth = vi.mocked(useAuth);

const NO_VERSION_COPY = "Este contenido aún no tiene una versión generada.";

beforeEach(() => {
  vi.clearAllMocks();
  mockUseAuth.mockReturnValue(makeAuth());
});

function makeAuth(role: string | null = "OWNER"): AuthContextValue {
  if (role === null) {
    return { status: "unauthenticated", login: vi.fn(), register: vi.fn(), logout: vi.fn(), refresh: vi.fn() };
  }
  return {
    status: "authenticated",
    session: {
      user: { id: "USR-1", email: "real.user@impulso.test", display_name: "Real User", status: "ACTIVE", preferences: { locale: null, timezone: null } },
      workspace: { id: "WS-1", name: "Real Workspace", slug: "real-workspace" },
      membership: { role },
    },
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn().mockResolvedValue(undefined),
  };
}

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

function makeApproval(overrides: Partial<ContentApprovalPublic> = {}): ContentApprovalPublic {
  return { id: "APR-1", status: "REQUESTED", decided_at: null, ...overrides };
}

function makeDetail(
  piece: ContentPiecePublic,
  latest_version: ContentVersionPublic | null = null,
  latest_approval: ContentApprovalPublic | null = null,
): ContentPieceDetailResponse {
  return { piece, latest_version, latest_approval, distribution: null };
}

describe("ContentDetailView", () => {
  it("shows mark-ready to a member and replaces detail only with the server response", async () => {
    const user = userEvent.setup();
    mockUseAuth.mockReturnValue(makeAuth("MEMBER"));
    const approved = makeDetail(makePiece({ status: "APPROVED" }));
    const ready = makeDetail(makePiece({ status: "READY_FOR_DISTRIBUTION" }));
    mockGetContentDetail.mockResolvedValue(approved);
    mockMarkReadyForDistribution.mockResolvedValue(ready);
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);
    await user.click(await screen.findByRole("button", { name: "Marcar listo para distribución" }));
    expect(mockMarkReadyForDistribution).toHaveBeenCalledWith("campaign-1", "piece-1");
    expect(await screen.findByText("Listo para distribución")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Registrar distribución realizada" })).not.toBeInTheDocument();
  });

  it("requires two clicks to record an outside event with optional reference", async () => {
    const user = userEvent.setup();
    const ready = makeDetail(makePiece({ status: "READY_FOR_DISTRIBUTION" }));
    const distributed = makeDetail(makePiece({ status: "DISTRIBUTED" }));
    mockGetContentDetail.mockResolvedValue(ready);
    mockRecordDistributed.mockResolvedValue(distributed);
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);
    await user.type(await screen.findByLabelText("Referencia externa (opcional)"), "post-123");
    await user.click(screen.getByRole("button", { name: "Registrar distribución realizada" }));
    expect(mockRecordDistributed).not.toHaveBeenCalled();
    expect(screen.getByText(/ya ocurrió fuera de esta aplicación/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirmar distribución realizada" }));
    expect(mockRecordDistributed).toHaveBeenCalledWith("campaign-1", "piece-1", "post-123");
  });

  it("shows the Distribution Evidence section only once the Piece and Distribution are both DISTRIBUTED", async () => {
    const distributed: ContentPieceDetailResponse = {
      piece: makePiece({ status: "DISTRIBUTED" }),
      latest_version: null,
      latest_approval: null,
      distribution: {
        id: "DST-1", status: "DISTRIBUTED", channel: "Instagram",
        external_reference: null, ready_at: "2026-01-01T00:00:00Z", distributed_at: "2026-01-02T00:00:00Z",
        tracking_requirement_ids: [],
      },
    };
    mockGetContentDetail.mockResolvedValue(distributed);
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);
    expect(await screen.findByText("Métricas reportadas para esta distribución")).toBeInTheDocument();
  });

  it("does not show the Distribution Evidence section while only READY_FOR_DISTRIBUTION", async () => {
    const ready: ContentPieceDetailResponse = {
      piece: makePiece({ status: "READY_FOR_DISTRIBUTION" }),
      latest_version: null,
      latest_approval: null,
      distribution: {
        id: "DST-1", status: "READY", channel: "Instagram",
        external_reference: null, ready_at: "2026-01-01T00:00:00Z", distributed_at: null,
        tracking_requirement_ids: [],
      },
    };
    mockGetContentDetail.mockResolvedValue(ready);
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);
    await screen.findByRole("heading", { name: "Reel" });
    expect(screen.queryByText("Métricas reportadas para esta distribución")).not.toBeInTheDocument();
  });

  describe("MVP-24: Tracking Requirement association", () => {
    function readyDetailWithAssociations(trackingRequirementIds: string[] = []): ContentPieceDetailResponse {
      return {
        piece: makePiece({ status: "READY_FOR_DISTRIBUTION" }),
        latest_version: null,
        latest_approval: null,
        distribution: {
          id: "DST-1", status: "READY", channel: "Instagram",
          external_reference: null, ready_at: "2026-01-01T00:00:00Z", distributed_at: null,
          tracking_requirement_ids: trackingRequirementIds,
        },
      };
    }

    function distributedDetailWithAssociations(trackingRequirementIds: string[] = []): ContentPieceDetailResponse {
      return {
        piece: makePiece({ status: "DISTRIBUTED" }),
        latest_version: null,
        latest_approval: null,
        distribution: {
          id: "DST-1", status: "DISTRIBUTED", channel: "Instagram",
          external_reference: null, ready_at: "2026-01-01T00:00:00Z", distributed_at: "2026-01-02T00:00:00Z",
          tracking_requirement_ids: trackingRequirementIds,
        },
      };
    }

    it("lets a MEMBER associate an available Tracking Requirement while READY", async () => {
      const user = userEvent.setup();
      mockUseAuth.mockReturnValue(makeAuth("MEMBER"));
      mockGetTracking.mockResolvedValue({
        plan: { id: "TRK-1", status: "NOT_DEFINED", requirements: [{ id: "TRQ-1", name: "Purchase event", status: null, associated_distribution_ids: [] }] },
      });
      const ready = readyDetailWithAssociations([]);
      const withAssociation = readyDetailWithAssociations(["TRQ-1"]);
      mockGetContentDetail.mockResolvedValue(ready);
      mockAssociateTrackingRequirement.mockResolvedValue(withAssociation);

      render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);
      await screen.findByText("Configuración de seguimiento asociada");
      expect(screen.getByText("Aún no hay requisitos de seguimiento asociados a esta distribución.")).toBeInTheDocument();

      const select = await screen.findByLabelText("Asociar requisito de seguimiento");
      await user.selectOptions(select, "TRQ-1");
      await user.click(screen.getByRole("button", { name: "Asociar" }));

      expect(mockAssociateTrackingRequirement).toHaveBeenCalledWith("campaign-1", "piece-1", "TRQ-1");
      expect(await screen.findByText("Purchase event")).toBeInTheDocument();
    });

    it("lets a MEMBER remove an association while READY", async () => {
      const user = userEvent.setup();
      mockUseAuth.mockReturnValue(makeAuth("MEMBER"));
      mockGetTracking.mockResolvedValue({
        plan: { id: "TRK-1", status: "NOT_DEFINED", requirements: [{ id: "TRQ-1", name: "Purchase event", status: "Configurado", associated_distribution_ids: ["DST-1"] }] },
      });
      const withAssociation = readyDetailWithAssociations(["TRQ-1"]);
      const removed = readyDetailWithAssociations([]);
      mockGetContentDetail.mockResolvedValue(withAssociation);
      mockDissociateTrackingRequirement.mockResolvedValue(removed);

      render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);
      await screen.findByText("Purchase event");
      expect(screen.getByText("(estado actual del requisito: Configurado)")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Quitar asociación" }));
      expect(mockDissociateTrackingRequirement).toHaveBeenCalledWith("campaign-1", "piece-1", "TRQ-1");
      expect(await screen.findByText("Aún no hay requisitos de seguimiento asociados a esta distribución.")).toBeInTheDocument();
    });

    it("hides add/remove controls once DISTRIBUTED but still shows the association", async () => {
      mockGetTracking.mockResolvedValue({
        plan: { id: "TRK-1", status: "NOT_DEFINED", requirements: [{ id: "TRQ-1", name: "Purchase event", status: null, associated_distribution_ids: ["DST-1"] }] },
      });
      mockGetContentDetail.mockResolvedValue(distributedDetailWithAssociations(["TRQ-1"]));

      render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);
      await screen.findByText("Purchase event");
      expect(screen.queryByRole("button", { name: "Quitar asociación" })).not.toBeInTheDocument();
      expect(screen.queryByLabelText("Asociar requisito de seguimiento")).not.toBeInTheDocument();
    });

    it("never implies distribution-time status — only 'estado actual'", async () => {
      mockGetTracking.mockResolvedValue({
        plan: { id: "TRK-1", status: "NOT_DEFINED", requirements: [{ id: "TRQ-1", name: "Purchase event", status: "Configurado", associated_distribution_ids: ["DST-1"] }] },
      });
      mockGetContentDetail.mockResolvedValue(readyDetailWithAssociations(["TRQ-1"]));

      render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);
      await screen.findByText("Purchase event");
      const text = document.body.textContent?.toLowerCase() ?? "";
      for (const forbidden of ["al momento de distribución", "cuando se publicó", "verificado", "tracking produjo", "tracking utilizado"]) {
        expect(text).not.toContain(forbidden);
      }
      expect(text).toContain("estado actual del requisito");
    });
  });
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
    mockGetContentDetail.mockResolvedValue(makeDetail(piece));
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
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece()));
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
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
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
      .mockResolvedValueOnce(makeDetail(makePiece()));

    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByText("No encontramos esta campaña, o no tienes acceso a ella.")).toBeInTheDocument();
    expect(screen.queryByText(/internal detail/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /reintentar/i }));

    expect(await screen.findByText(NO_VERSION_COPY)).toBeInTheDocument();
  });

  // --- MVP-17B: lifecycle + approval controls -------------------------

  it("shows Marcar en producción for a DRAFT piece and advances on success", async () => {
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "DRAFT" })));
    mockMarkContentInProduction.mockResolvedValue(makeDetail(makePiece({ status: "IN_PRODUCTION" })));
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    const button = await screen.findByRole("button", { name: "Marcar en producción" });
    await user.click(button);

    expect(mockMarkContentInProduction).toHaveBeenCalledWith("campaign-1", "piece-1");
    expect(await screen.findByRole("button", { name: "Marcar producido" })).toBeInTheDocument();
  });

  // --- MVP-20: revision loop -------------------------------------------

  it("shows only Crear versión revisada for REVISION_REQUESTED, never Marcar en producción or Solicitar aprobación", async () => {
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "REVISION_REQUESTED" })));
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByRole("button", { name: "Crear versión revisada" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Marcar en producción" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Solicitar aprobación" })).not.toBeInTheDocument();
  });

  it("shows Crear versión revisada to any active member, not just OWNER/ADMIN", async () => {
    mockUseAuth.mockReturnValue(makeAuth("MEMBER"));
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "REVISION_REQUESTED" })));
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByRole("button", { name: "Crear versión revisada" })).toBeInTheDocument();
  });

  it("opens the revision form pre-filled from the latest version's payload", async () => {
    const version = makeVersion({ hook: "Gancho original" });
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "REVISION_REQUESTED" }), version));
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    await user.click(await screen.findByRole("button", { name: "Crear versión revisada" }));

    const textarea = screen.getByLabelText("Contenido de la versión revisada") as HTMLTextAreaElement;
    expect(textarea.value).toContain("Gancho original");
  });

  it("rejects invalid JSON client-side without calling the backend", async () => {
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "REVISION_REQUESTED" }), makeVersion({ hook: "x" })));
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    await user.click(await screen.findByRole("button", { name: "Crear versión revisada" }));
    const textarea = screen.getByLabelText("Contenido de la versión revisada");
    fireEvent.change(textarea, { target: { value: "not valid json" } });
    await user.click(screen.getByRole("button", { name: "Crear versión revisada" }));

    expect(await screen.findByText("El contenido debe ser JSON válido.")).toBeInTheDocument();
    expect(mockCreateContentRevisionVersion).not.toHaveBeenCalled();
  });

  it("submits the edited payload and, on success, the form disappears with the server's new state", async () => {
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "REVISION_REQUESTED" }), makeVersion({ hook: "Original" })));
    const newVersion = makeVersion({ hook: "Gancho revisado" });
    mockCreateContentRevisionVersion.mockResolvedValue(makeDetail(makePiece({ status: "IN_PRODUCTION" }), newVersion));
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    await user.click(await screen.findByRole("button", { name: "Crear versión revisada" }));
    const textarea = screen.getByLabelText("Contenido de la versión revisada");
    fireEvent.change(textarea, { target: { value: JSON.stringify({ hook: "Gancho revisado" }) } });
    await user.click(screen.getByRole("button", { name: "Crear versión revisada" }));

    expect(mockCreateContentRevisionVersion).toHaveBeenCalledWith("campaign-1", "piece-1", { payload: { hook: "Gancho revisado" } });
    expect(await screen.findByRole("button", { name: "Marcar producido" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Contenido de la versión revisada")).not.toBeInTheDocument();
    expect(screen.getByText("Gancho revisado")).toBeInTheDocument();
  });

  it("on a failed revision submission, keeps the form open with the typed content preserved", async () => {
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "REVISION_REQUESTED" }), makeVersion({ hook: "Original" })));
    mockCreateContentRevisionVersion.mockRejectedValue(new ApiError(409, "INVALID_LIFECYCLE_TRANSITION", "internal detail"));
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    await user.click(await screen.findByRole("button", { name: "Crear versión revisada" }));
    const textarea = screen.getByLabelText("Contenido de la versión revisada");
    fireEvent.change(textarea, { target: { value: JSON.stringify({ hook: "Todavía editando" }) } });
    await user.click(screen.getByRole("button", { name: "Crear versión revisada" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(/internal detail/i)).not.toBeInTheDocument();
    const stillOpen = screen.getByLabelText("Contenido de la versión revisada") as HTMLTextAreaElement;
    expect(stillOpen.value).toContain("Todavía editando");
  });

  it("cancels the revision form without calling the backend", async () => {
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "REVISION_REQUESTED" }), makeVersion({ hook: "x" })));
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    await user.click(await screen.findByRole("button", { name: "Crear versión revisada" }));
    await user.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(screen.queryByLabelText("Contenido de la versión revisada")).not.toBeInTheDocument();
    expect(mockCreateContentRevisionVersion).not.toHaveBeenCalled();
  });

  it("shows Marcar producido for IN_PRODUCTION and advances on success", async () => {
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "IN_PRODUCTION" })));
    mockMarkContentProduced.mockResolvedValue(makeDetail(makePiece({ status: "PRODUCED" })));
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    const button = await screen.findByRole("button", { name: "Marcar producido" });
    await user.click(button);

    expect(mockMarkContentProduced).toHaveBeenCalledWith("campaign-1", "piece-1");
    expect(await screen.findByRole("button", { name: "Marcar listo para revisión" })).toBeInTheDocument();
  });

  it("shows Marcar listo para revisión for PRODUCED", async () => {
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "PRODUCED" })));
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByRole("button", { name: "Marcar listo para revisión" })).toBeInTheDocument();
  });

  it("shows Solicitar aprobación for READY_FOR_REVIEW with no open approval", async () => {
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "READY_FOR_REVIEW" })));
    mockRequestContentApproval.mockResolvedValue(
      makeDetail(makePiece({ status: "READY_FOR_REVIEW" }), null, makeApproval({ status: "REQUESTED" })),
    );
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    const button = await screen.findByRole("button", { name: "Solicitar aprobación" });
    await user.click(button);

    expect(mockRequestContentApproval).toHaveBeenCalledWith("campaign-1", "piece-1");
    expect(await screen.findByRole("button", { name: "Marcar en revisión" })).toBeInTheDocument();
  });

  it("does not show Solicitar aprobación while an approval is already open", async () => {
    mockGetContentDetail.mockResolvedValue(
      makeDetail(makePiece({ status: "READY_FOR_REVIEW" }), null, makeApproval({ status: "UNDER_REVIEW" })),
    );
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    await screen.findByText(/En revisión/);
    expect(screen.queryByRole("button", { name: "Solicitar aprobación" })).not.toBeInTheDocument();
  });

  it("shows Marcar en revisión when the latest approval is REQUESTED, for any active member", async () => {
    mockUseAuth.mockReturnValue(makeAuth("MEMBER"));
    mockGetContentDetail.mockResolvedValue(
      makeDetail(makePiece({ status: "READY_FOR_REVIEW" }), null, makeApproval({ status: "REQUESTED" })),
    );
    mockMarkContentApprovalUnderReview.mockResolvedValue(
      makeDetail(makePiece({ status: "READY_FOR_REVIEW" }), null, makeApproval({ status: "UNDER_REVIEW" })),
    );
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    const button = await screen.findByRole("button", { name: "Marcar en revisión" });
    await user.click(button);

    expect(mockMarkContentApprovalUnderReview).toHaveBeenCalledWith("campaign-1", "piece-1", "APR-1");
    expect(await screen.findByText(/En revisión/)).toBeInTheDocument();
  });

  it("shows final-decision controls for UNDER_REVIEW when the caller is OWNER/ADMIN", async () => {
    mockUseAuth.mockReturnValue(makeAuth("ADMIN"));
    mockGetContentDetail.mockResolvedValue(
      makeDetail(makePiece({ status: "READY_FOR_REVIEW" }), null, makeApproval({ status: "UNDER_REVIEW" })),
    );
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    expect(await screen.findByRole("heading", { name: "Registrar decisión de aprobación" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aprobar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Solicitar cambios" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rechazar" })).toBeInTheDocument();
  });

  it("does not expose an enabled final-decision action for UNDER_REVIEW when the caller is MEMBER", async () => {
    mockUseAuth.mockReturnValue(makeAuth("MEMBER"));
    mockGetContentDetail.mockResolvedValue(
      makeDetail(makePiece({ status: "READY_FOR_REVIEW" }), null, makeApproval({ status: "UNDER_REVIEW" })),
    );
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    await screen.findByText(/En revisión/);
    expect(screen.queryByRole("button", { name: "Aprobar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rechazar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Solicitar cambios" })).not.toBeInTheDocument();
  });

  it("requires a second click to confirm APPROVED before calling the backend", async () => {
    mockUseAuth.mockReturnValue(makeAuth("OWNER"));
    mockGetContentDetail.mockResolvedValue(
      makeDetail(makePiece({ status: "READY_FOR_REVIEW" }), null, makeApproval({ status: "UNDER_REVIEW" })),
    );
    mockRecordContentApprovalDecision.mockResolvedValue(
      makeDetail(makePiece({ status: "APPROVED" }), null, makeApproval({ status: "APPROVED", decided_at: "2026-01-03T00:00:00Z" })),
    );
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    const approveButton = await screen.findByRole("button", { name: "Aprobar" });
    await user.click(approveButton);
    expect(mockRecordContentApprovalDecision).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Confirmar" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Confirmar" }));
    expect(mockRecordContentApprovalDecision).toHaveBeenCalledWith("campaign-1", "piece-1", "APR-1", "APPROVED");
  });

  it("submits CHANGES_REQUESTED immediately, without a confirmation step", async () => {
    mockGetContentDetail.mockResolvedValue(
      makeDetail(makePiece({ status: "READY_FOR_REVIEW" }), null, makeApproval({ status: "UNDER_REVIEW" })),
    );
    mockRecordContentApprovalDecision.mockResolvedValue(
      makeDetail(makePiece({ status: "READY_FOR_REVIEW" }), null, makeApproval({ status: "CHANGES_REQUESTED" })),
    );
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    const button = await screen.findByRole("button", { name: "Solicitar cambios" });
    await user.click(button);

    expect(mockRecordContentApprovalDecision).toHaveBeenCalledWith("campaign-1", "piece-1", "APR-1", "CHANGES_REQUESTED");
  });

  it("replaces state with the exact server response on a successful mutation, never optimistically", async () => {
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "PRODUCED" })));
    const serverVersion = makeVersion({ note: "server-truth" });
    mockMarkContentReadyForReview.mockResolvedValue(makeDetail(makePiece({ status: "READY_FOR_REVIEW" }), serverVersion));
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    await user.click(await screen.findByRole("button", { name: "Marcar listo para revisión" }));

    expect(await screen.findByText("server-truth")).toBeInTheDocument();
  });

  it("on a failed mutation, preserves the last confirmed state and allows retry", async () => {
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "DRAFT" })));
    mockMarkContentInProduction
      .mockRejectedValueOnce(new ApiError(409, "INVALID_LIFECYCLE_TRANSITION", "internal detail"))
      .mockResolvedValueOnce(makeDetail(makePiece({ status: "IN_PRODUCTION" })));
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    const button = await screen.findByRole("button", { name: "Marcar en producción" });
    await user.click(button);

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    // State remains DRAFT — the same control is still offered, retryable.
    expect(screen.getByRole("button", { name: "Marcar en producción" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Marcar en producción" }));
    expect(await screen.findByRole("button", { name: "Marcar producido" })).toBeInTheDocument();
  });

  it("disables lifecycle controls while a mutation is in flight (single-flight)", async () => {
    mockGetContentDetail.mockResolvedValue(makeDetail(makePiece({ status: "DRAFT" })));
    let resolveMutation: (value: ContentPieceDetailResponse) => void = () => {};
    mockMarkContentInProduction.mockImplementation(
      () => new Promise((resolve) => { resolveMutation = resolve; }),
    );
    const user = userEvent.setup();
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    const button = await screen.findByRole("button", { name: "Marcar en producción" });
    await user.click(button);

    expect(button).toBeDisabled();
    resolveMutation(makeDetail(makePiece({ status: "IN_PRODUCTION" })));
    expect(await screen.findByRole("button", { name: "Marcar producido" })).toBeInTheDocument();
  });

  it("never renders the phrase 'Aprobar para distribución' or implies automated governance validation", async () => {
    mockUseAuth.mockReturnValue(makeAuth("OWNER"));
    mockGetContentDetail.mockResolvedValue(
      makeDetail(makePiece({ status: "READY_FOR_REVIEW" }), null, makeApproval({ status: "UNDER_REVIEW" })),
    );
    render(<ContentDetailView campaignId="campaign-1" contentId="piece-1" />);

    await screen.findByRole("heading", { name: "Registrar decisión de aprobación" });
    expect(screen.queryByText(/aprobar para distribución/i)).not.toBeInTheDocument();
  });
});
