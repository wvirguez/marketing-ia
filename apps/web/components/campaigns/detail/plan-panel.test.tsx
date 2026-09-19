import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PlanPanel } from "@/components/campaigns/detail/plan-panel";
import { ApiError } from "@/lib/api/client";
import type { ContentBriefPublic, ContentPlanPublic, PlanItemPublic, PlanOutputResponse } from "@/types/planning";
import type { ExperimentPublic, StrategyOutputResponse } from "@/types/strategy";
import type { ContentPieceDetailResponse } from "@/types/content";

vi.mock("@/lib/api/planning", () => ({
  getPlan: vi.fn(),
  createPlan: vi.fn(),
  createBrief: vi.fn(),
}));
vi.mock("@/lib/api/content", () => ({
  createContentPiece: vi.fn(),
}));
vi.mock("@/lib/api/strategy", () => ({
  getStrategy: vi.fn(),
}));
vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: vi.fn(),
}));

import { createContentPiece } from "@/lib/api/content";
import { createBrief, createPlan, getPlan } from "@/lib/api/planning";
import { getStrategy } from "@/lib/api/strategy";
import { useAuth } from "@/lib/auth/auth-context";

const mockGetPlan = vi.mocked(getPlan);
const mockCreatePlan = vi.mocked(createPlan);
const mockCreateBrief = vi.mocked(createBrief);
const mockCreateContentPiece = vi.mocked(createContentPiece);
const mockGetStrategy = vi.mocked(getStrategy);
const mockUseAuth = vi.mocked(useAuth);

function mockAuth(role: "OWNER" | "ADMIN" | "MEMBER" | null) {
  if (role === null) {
    mockUseAuth.mockReturnValue({
      status: "unauthenticated",
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      refresh: vi.fn(),
    } as never);
    return;
  }
  mockUseAuth.mockReturnValue({
    status: "authenticated",
    session: {
      user: { id: "USR-1", email: "user@impulso.test", display_name: "User", status: "ACTIVE", preferences: { locale: null, timezone: null } },
      workspace: { id: "WS-1", name: "Workspace", slug: "workspace" },
      membership: { role },
    },
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  } as never);
}

function experiment(overrides: Partial<ExperimentPublic> = {}): ExperimentPublic {
  return {
    id: "EXP-1",
    hypothesis_id: "HYP-1",
    description: "A/B test two onboarding email sequences.",
    status: "RECORDED",
    created_at: "2026-01-01T00:00:00Z",
    comparison_label: "NO_COMPARISON_DECLARED",
    definition: null,
    ...overrides,
  };
}

function strategyOutput(overrides: Partial<StrategyOutputResponse> = {}): StrategyOutputResponse {
  return {
    strategy: { id: "STR-1", campaign_id: "campaign-1", version: 1, origin: "BOOTSTRAP", summary: "x", created_at: "2026-01-01T00:00:00Z" },
    positioning: { id: "POS-1", statement: "x", created_at: "2026-01-01T00:00:00Z" },
    hypotheses: [],
    experiments: [],
    ...overrides,
  };
}

function plan(overrides: Partial<ContentPlanPublic> = {}): ContentPlanPublic {
  return {
    id: "PLN-1",
    campaign_id: "campaign-1",
    experiment_id: null,
    version: 1,
    summary: "Existing plan summary.",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function planItem(overrides: Partial<PlanItemPublic> = {}): PlanItemPublic {
  return {
    id: "ITM-1",
    format: "Reel",
    objective: "Introduce the offer.",
    sequence: 1,
    scheduled_date: null,
    created_at: "2026-01-01T00:00:00Z",
    brief: null,
    ...overrides,
  };
}

function contentPieceDetail(overrides: Partial<ContentPieceDetailResponse> = {}): ContentPieceDetailResponse {
  return {
    piece: {
      id: "CNT-1",
      format: "Reel",
      objective: "Generate identification and interest.",
      funnel_stage: "Awareness",
      cta: "Learn the method",
      channel: "Instagram",
      status: "DRAFT",
      archived_at: null,
      created_at: "2026-01-01T00:00:00Z",
    },
    latest_version: { id: "CNV-1", payload: {}, created_at: "2026-01-01T00:00:00Z" },
    latest_approval: null,
    distribution: null,
    ...overrides,
  };
}

function contentBrief(overrides: Partial<ContentBriefPublic> = {}): ContentBriefPublic {
  return {
    id: "CBRF-1",
    plan_item_id: "ITM-1",
    content_plan_id: "PLN-1",
    brief: "Produce a beginner-friendly reel.",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function output(overrides: Partial<PlanOutputResponse> = {}): PlanOutputResponse {
  return { plan: plan(), items: [planItem()], ...overrides };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetStrategy.mockResolvedValue(strategyOutput());
});

describe("PlanPanel — existing read behavior remains intact", () => {
  it("renders the current plan summary and items", async () => {
    mockAuth(null);
    mockGetPlan.mockResolvedValue(output());
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Existing plan summary.")).toBeInTheDocument());
    expect(screen.getByText("Introduce the offer.")).toBeInTheDocument();
  });

  it("shows the before-start copy when no plan exists yet", async () => {
    mockAuth(null);
    mockGetPlan.mockResolvedValue({ plan: null, items: [] });
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() =>
      expect(
        screen.getByText("Aún no se ha generado el borrador de planificación para esta campaña."),
      ).toBeInTheDocument(),
    );
  });
});

describe("PlanPanel — Plan creation authority", () => {
  it("MEMBER sees the proposal control", async () => {
    mockAuth("MEMBER");
    mockGetPlan.mockResolvedValue(output());
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
  });

  it("VIEWER/unauthenticated never sees the proposal control", async () => {
    mockAuth(null);
    mockGetPlan.mockResolvedValue(output());
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Existing plan summary.")).toBeInTheDocument());
    expect(screen.queryByText("Proponer plan")).not.toBeInTheDocument();
  });

  it("the proposal control is also available before any plan has been generated", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue({ plan: null, items: [] });
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
  });
});

describe("PlanPanel — Plan creation submission", () => {
  it("the confirm button stays disabled for a blank summary", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output());
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer plan"));
    expect(screen.getByText("Confirmar plan")).toBeDisabled();
  });

  it("the experiment selector defaults to no selection and shows current-Strategy experiments", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output());
    mockGetStrategy.mockResolvedValue(strategyOutput({ experiments: [experiment()] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer plan"));

    const select = (await screen.findByLabelText(
      "Experimento que operacionaliza este plan (opcional)",
    )) as HTMLSelectElement;
    expect(select.value).toBe("");
    await waitFor(() => expect(screen.getByText("EXP-1 — A/B test two onboarding email sequences. (sin comparación declarada)")).toBeInTheDocument());
  });

  it("MVP-37: marks a definition-less experiment in the selector but never gates selection or submission", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValueOnce(output({ plan: null, items: [] }));
    mockGetStrategy.mockResolvedValue(
      strategyOutput({
        experiments: [
          experiment(),
          experiment({
            id: "EXP-2",
            description: "A second experiment.",
            comparison_label: "DECLARED_OBSERVATIONAL_INTENT",
            definition: {
              id: "EXD-1",
              experiment_id: "EXP-2",
              version: 1,
              comparison_question: "q",
              comparison_type: "OBSERVATIONAL",
              changed_factor: "f",
              controlled_factors: [],
              comparison_basis: "b",
              scope: "s",
              learning_intent: "l",
              non_conclusion_boundary: "n",
              non_conclusion_codes: [],
              created_at: "2026-01-01T00:00:00Z",
            },
          }),
        ],
      }),
    );
    mockCreatePlan.mockResolvedValue(plan({ experiment_id: "EXP-1" }));
    mockGetPlan.mockResolvedValueOnce(output({ plan: plan({ experiment_id: "EXP-1" }) }));

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer plan"));
    await userEvent.type(screen.getByLabelText("Nuevo plan"), "Plan for an experiment with no definition.");
    await waitFor(() =>
      expect(
        screen.getByText("EXP-1 — A/B test two onboarding email sequences. (sin comparación declarada)"),
      ).toBeInTheDocument(),
    );
    // The defined experiment carries no suffix.
    expect(screen.getByText("EXP-2 — A second experiment.")).toBeInTheDocument();
    // Selecting the definition-less experiment is still allowed and submits normally.
    await userEvent.selectOptions(screen.getByLabelText("Experimento que operacionaliza este plan (opcional)"), "EXP-1");
    expect(screen.getByText("Confirmar plan")).toBeEnabled();
    await userEvent.click(screen.getByText("Confirmar plan"));
    await waitFor(() =>
      expect(mockCreatePlan).toHaveBeenCalledWith("campaign-1", "Plan for an experiment with no definition.", "EXP-1"),
    );
  });

  it("generic submission sends no experiment_public_id", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValueOnce(output({ plan: null, items: [] }));
    mockCreatePlan.mockResolvedValue(plan());
    mockGetPlan.mockResolvedValueOnce(output());

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer plan"));
    await userEvent.type(screen.getByLabelText("Nuevo plan"), "A generic content plan.");
    await userEvent.click(screen.getByText("Confirmar plan"));

    await waitFor(() =>
      expect(mockCreatePlan).toHaveBeenCalledWith("campaign-1", "A generic content plan.", null),
    );
    await waitFor(() => expect(screen.getByText("Existing plan summary.")).toBeInTheDocument());
  });

  it("experiment-derived submission sends the selected public id", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValueOnce(output({ plan: null, items: [] }));
    mockGetStrategy.mockResolvedValue(strategyOutput({ experiments: [experiment()] }));
    mockCreatePlan.mockResolvedValue(plan({ experiment_id: "EXP-1" }));
    mockGetPlan.mockResolvedValueOnce(output({ plan: plan({ experiment_id: "EXP-1" }) }));

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer plan"));
    await userEvent.type(screen.getByLabelText("Nuevo plan"), "Operationalizes the experiment.");
    await waitFor(() => expect(screen.getByText("EXP-1 — A/B test two onboarding email sequences. (sin comparación declarada)")).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText("Experimento que operacionaliza este plan (opcional)"), "EXP-1");
    await userEvent.click(screen.getByText("Confirmar plan"));

    await waitFor(() =>
      expect(mockCreatePlan).toHaveBeenCalledWith("campaign-1", "Operationalizes the experiment.", "EXP-1"),
    );
  });

  it("surfaces a mutation error without losing the form", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output());
    mockCreatePlan.mockRejectedValue(new ApiError(409, "VERSION_CONFLICT", "Conflict."));

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer plan"));
    await userEvent.type(screen.getByLabelText("Nuevo plan"), "x");
    await userEvent.click(screen.getByText("Confirmar plan"));

    await waitFor(() => expect(screen.getByText("No pudimos completar esta acción. Intenta de nuevo.")).toBeInTheDocument());
    expect(screen.getByLabelText("Nuevo plan")).toBeInTheDocument();
  });

  it("cancel clears the form without submitting", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output());
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Proponer plan")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Proponer plan"));
    await userEvent.type(screen.getByLabelText("Nuevo plan"), "discarded");
    await userEvent.click(screen.getByText("Cancelar"));

    expect(screen.queryByLabelText("Nuevo plan")).not.toBeInTheDocument();
    expect(mockCreatePlan).not.toHaveBeenCalled();
  });
});

describe("PlanPanel — Content Brief creation (MVP-34A/-34B)", () => {
  it("MEMBER+ sees the create affordance for an unbriefed item", async () => {
    mockAuth("MEMBER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: null })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Redactar brief")).toBeInTheDocument());
  });

  it("unauthenticated never sees the create affordance", async () => {
    mockAuth(null);
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: null })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Introduce the offer.")).toBeInTheDocument());
    expect(screen.queryByText("Redactar brief")).not.toBeInTheDocument();
  });

  it("submits the exact payload for the targeted Plan Item", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValueOnce(output({ items: [planItem({ id: "ITM-7", brief: null })] }));
    mockCreateBrief.mockResolvedValue(contentBrief({ plan_item_id: "ITM-7" }));
    mockGetPlan.mockResolvedValueOnce(
      output({ items: [planItem({ id: "ITM-7", brief: contentBrief({ plan_item_id: "ITM-7" }) })] }),
    );

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Redactar brief")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Redactar brief"));
    await userEvent.type(screen.getByLabelText("Brief para este elemento"), "Produce a beginner reel.");
    await userEvent.click(screen.getByText("Confirmar brief"));

    await waitFor(() =>
      expect(mockCreateBrief).toHaveBeenCalledWith("campaign-1", "ITM-7", "Produce a beginner reel."),
    );
  });

  it("re-renders the persisted Brief read-only after a successful create", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValueOnce(output({ items: [planItem({ brief: null })] }));
    mockCreateBrief.mockResolvedValue(contentBrief());
    mockGetPlan.mockResolvedValueOnce(output({ items: [planItem({ brief: contentBrief() })] }));

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Redactar brief")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Redactar brief"));
    await userEvent.type(screen.getByLabelText("Brief para este elemento"), "x");
    await userEvent.click(screen.getByText("Confirmar brief"));

    await waitFor(() =>
      expect(screen.getByText("Brief: Produce a beginner-friendly reel.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Redactar brief")).not.toBeInTheDocument();
  });

  it("displays an existing Brief read-only, with no edit/delete/re-brief affordance", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: contentBrief() })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() =>
      expect(screen.getByText("Brief: Produce a beginner-friendly reel.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Redactar brief")).not.toBeInTheDocument();
    expect(screen.queryByText("Editar")).not.toBeInTheDocument();
    expect(screen.queryByText("Eliminar")).not.toBeInTheDocument();
  });

  it("surfaces a mutation error (e.g. 409 duplicate) without losing the form", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: null })] }));
    mockCreateBrief.mockRejectedValue(new ApiError(409, "PLAN_ITEM_ALREADY_BRIEFED", "Conflict."));

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Redactar brief")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Redactar brief"));
    await userEvent.type(screen.getByLabelText("Brief para este elemento"), "x");
    await userEvent.click(screen.getByText("Confirmar brief"));

    await waitFor(() => expect(screen.getByText("No pudimos completar esta acción. Intenta de nuevo.")).toBeInTheDocument());
    expect(screen.getByLabelText("Brief para este elemento")).toBeInTheDocument();
  });

  it("cancel clears the brief form without submitting", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: null })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Redactar brief")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Redactar brief"));
    await userEvent.type(screen.getByLabelText("Brief para este elemento"), "discarded");
    await userEvent.click(screen.getByText("Cancelar"));

    expect(screen.queryByLabelText("Brief para este elemento")).not.toBeInTheDocument();
    expect(mockCreateBrief).not.toHaveBeenCalled();
  });

  it("does not render any Variant or experimental-arm affordance", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: null })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Redactar brief")).toBeInTheDocument());
    expect(screen.queryByText(/variant/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/control/i)).not.toBeInTheDocument();
  });
});

async function fillPieceForm(itemId: string) {
  await userEvent.type(screen.getByLabelText("Formato"), "Reel");
  await userEvent.type(screen.getByLabelText("Objetivo"), "Generate identification and interest.");
  await userEvent.type(screen.getByLabelText("Etapa del embudo"), "Awareness");
  await userEvent.type(screen.getByLabelText("Llamado a la acción"), "Learn the method");
  await userEvent.type(screen.getByLabelText("Canal"), "Instagram");
  void itemId;
}

describe("PlanPanel — Content Piece creation (MVP-35A/-35B)", () => {
  it("MEMBER+ sees the create affordance under a briefed item", async () => {
    mockAuth("MEMBER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: contentBrief() })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Crear pieza")).toBeInTheDocument());
  });

  it("unauthenticated never sees the create affordance", async () => {
    mockAuth(null);
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: contentBrief() })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText(/Brief:/)).toBeInTheDocument());
    expect(screen.queryByText("Crear pieza")).not.toBeInTheDocument();
  });

  it("the create affordance is absent for an unbriefed item", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: null })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Redactar brief")).toBeInTheDocument());
    expect(screen.queryByText("Crear pieza")).not.toBeInTheDocument();
  });

  it("submits the exact payload for the targeted ContentBrief", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(
      output({ items: [planItem({ id: "ITM-7", brief: contentBrief({ id: "CBRF-7", plan_item_id: "ITM-7" }) })] }),
    );
    mockCreateContentPiece.mockResolvedValue(contentPieceDetail());

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Crear pieza")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Crear pieza"));
    await fillPieceForm("ITM-7");
    await userEvent.click(screen.getByText("Confirmar pieza"));

    await waitFor(() =>
      expect(mockCreateContentPiece).toHaveBeenCalledWith("campaign-1", "CBRF-7", {
        format: "Reel",
        objective: "Generate identification and interest.",
        funnel_stage: "Awareness",
        cta: "Learn the method",
        channel: "Instagram",
        payload: {},
      }),
    );
  });

  it("shows a success confirmation and resets the form after creation", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: contentBrief() })] }));
    mockCreateContentPiece.mockResolvedValue(contentPieceDetail({ piece: { ...contentPieceDetail().piece, id: "CNT-99" } }));

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Crear pieza")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Crear pieza"));
    await fillPieceForm("ITM-1");
    await userEvent.click(screen.getByText("Confirmar pieza"));

    await waitFor(() => expect(screen.getByText(/Pieza creada: CNT-99/)).toBeInTheDocument());
    expect(screen.queryByLabelText("Formato")).not.toBeInTheDocument();
  });

  it("multiple items do not overwrite each other's success state", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(
      output({
        items: [
          planItem({ id: "ITM-1", brief: contentBrief({ id: "CBRF-1", plan_item_id: "ITM-1" }) }),
          planItem({ id: "ITM-2", sequence: 2, brief: contentBrief({ id: "CBRF-2", plan_item_id: "ITM-2" }) }),
        ],
      }),
    );
    mockCreateContentPiece.mockResolvedValue(contentPieceDetail({ piece: { ...contentPieceDetail().piece, id: "CNT-1" } }));

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getAllByText("Crear pieza")).toHaveLength(2));
    await userEvent.click(screen.getAllByText("Crear pieza")[0]);
    await fillPieceForm("ITM-1");
    await userEvent.click(screen.getByText("Confirmar pieza"));

    await waitFor(() => expect(screen.getByText(/Pieza creada: CNT-1/)).toBeInTheDocument());
    // Cardinality is 0..N (MVP-35A §D) — both items' own "Crear pieza"
    // affordances remain available/independent after one succeeds.
    expect(screen.getAllByText("Crear pieza")).toHaveLength(2);
  });

  it("surfaces a mutation error without losing the form", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: contentBrief() })] }));
    mockCreateContentPiece.mockRejectedValue(new ApiError(409, "SOME_CONFLICT", "Conflict."));

    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Crear pieza")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Crear pieza"));
    await fillPieceForm("ITM-1");
    await userEvent.click(screen.getByText("Confirmar pieza"));

    await waitFor(() => expect(screen.getByText("No pudimos completar esta acción. Intenta de nuevo.")).toBeInTheDocument());
    expect(screen.getByLabelText("Formato")).toBeInTheDocument();
  });

  it("cancel clears the piece form without submitting", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: contentBrief() })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Crear pieza")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Crear pieza"));
    await userEvent.type(screen.getByLabelText("Formato"), "discarded");
    await userEvent.click(screen.getByText("Cancelar"));

    expect(screen.queryByLabelText("Formato")).not.toBeInTheDocument();
    expect(mockCreateContentPiece).not.toHaveBeenCalled();
  });

  it("does not render any Variant or Experiment-execution UI", async () => {
    mockAuth("OWNER");
    mockGetPlan.mockResolvedValue(output({ items: [planItem({ brief: contentBrief() })] }));
    render(<PlanPanel campaignId="campaign-1" active={true} refreshToken={0} />);
    await waitFor(() => expect(screen.getByText("Crear pieza")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Crear pieza"));
    expect(screen.queryByText(/variant/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/experimento/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/estado/i)).not.toBeInTheDocument();
  });
});
