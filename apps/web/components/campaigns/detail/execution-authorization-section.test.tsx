import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExecutionAuthorizationSection } from "@/components/campaigns/detail/execution-authorization-section";
import { ApiError } from "@/lib/api/client";
import type {
  ExecutionAuthorizationHistoryResponse,
  ExecutionAuthorizationPublic,
  ExperimentDefinitionPublic,
  ExperimentPublic,
} from "@/types/strategy";

vi.mock("@/lib/api/strategy", () => ({
  authorizeExecution: vi.fn(),
  getExecutionAuthorization: vi.fn(),
  getExecutionAuthorizationHistory: vi.fn(),
  revokeExecutionAuthorization: vi.fn(),
  startExecution: vi.fn(),
  // Experiment Evidence Binding: the claims section mounts beneath a started attempt.
  listEvidenceClaims: vi.fn(),
  createEvidenceClaim: vi.fn(),
  disposeEvidenceClaim: vi.fn(),
  getMeasurementContract: vi.fn(),
}));

import {
  authorizeExecution,
  getExecutionAuthorization,
  getExecutionAuthorizationHistory,
  revokeExecutionAuthorization,
  listEvidenceClaims,
} from "@/lib/api/strategy";

const mockAuthorize = vi.mocked(authorizeExecution);
const mockGet = vi.mocked(getExecutionAuthorization);
const mockHistory = vi.mocked(getExecutionAuthorizationHistory);
const mockRevoke = vi.mocked(revokeExecutionAuthorization);

let uuidCounter = 0;

function definition(overrides: Partial<ExperimentDefinitionPublic> = {}): ExperimentDefinitionPublic {
  return {
    id: "EXD-1",
    experiment_id: "EXP-1",
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
    variant_count: 1,
    is_pinned: true,
    has_measurement_contract: true,
    measurement_contract_version: 1,
    ...overrides,
  };
}

const EXPERIMENT: ExperimentPublic = {
  id: "EXP-1",
  hypothesis_id: "HYP-1",
  description: "Compare two hooks.",
  status: "RECORDED",
  created_at: "2026-01-01T00:00:00Z",
  comparison_label: "DECLARED_OBSERVATIONAL_INTENT",
  definition: null,
};

function authorization(overrides: Partial<ExecutionAuthorizationPublic> = {}): ExecutionAuthorizationPublic {
  return {
    id: "EXA-1",
    experiment_id: "EXP-1",
    definition_version_id: "EXD-1",
    contract_version_id: "MSC-1",
    unit_of_assignment: "Visitante (sesión)",
    allocation_design: "División 50/50 por sesión.",
    variants: [{ id: "VAR-1", label: "Condición A", condition_description: "Pregunta directa." }],
    signal_count: 2,
    tracking_required_signal_count: 1,
    active: true,
    revoked_at: null,
    revoked_reason: null,
    superseded_by: null,
    execution_start: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function history(items: ExecutionAuthorizationPublic[]): ExecutionAuthorizationHistoryResponse {
  return {
    experiment_id: "EXP-1",
    current_id: items.find((item) => item.active)?.id ?? null,
    authorizations: items,
  };
}

function renderSection(def: ExperimentDefinitionPublic, role: string | null = "OWNER") {
  const onChanged = vi.fn();
  render(
    <ExecutionAuthorizationSection campaignId="campaign-1" experiment={EXPERIMENT} definition={def} role={role} onChanged={onChanged} />,
  );
  return { onChanged };
}

function fillAuthorize(unit = "Visitante (sesión)", design = "División 50/50 por sesión.") {
  fireEvent.change(screen.getByLabelText("Unidad de asignación"), { target: { value: unit } });
  fireEvent.change(screen.getByLabelText("Diseño de asignación"), { target: { value: design } });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listEvidenceClaims).mockResolvedValue({ experiment_id: "EXP-1", start_id: "", claims: [] });
  mockGet.mockResolvedValue(null);
  mockHistory.mockResolvedValue(history([]));
  uuidCounter = 0;
  vi.spyOn(crypto, "randomUUID").mockImplementation(() => `uuid-${++uuidCounter}` as ReturnType<typeof crypto.randomUUID>);
});

describe("ExecutionAuthorizationSection — state and dependencies", () => {
  it("shows no active authorization and never fetches while the definition is not pinned", async () => {
    renderSection(definition({ is_pinned: false, has_measurement_contract: false, variant_count: 0 }));
    expect(screen.getByText("No hay una autorización activa para este experimento.")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mockGet).not.toHaveBeenCalled();
    expect(mockHistory).not.toHaveBeenCalled();
  });

  it("lists satisfied and pending dependencies, and what is NOT required", () => {
    renderSection(definition({ variant_count: 0, has_measurement_contract: false, is_pinned: false }));
    expect(screen.getByText(/Condiciones declaradas \(0 de al menos 1\)/)).toHaveTextContent("pendiente");
    expect(screen.getByText(/Contrato de medición: pendiente/)).toBeInTheDocument();
    expect(screen.getByText(/No se exige al autorizar/)).toHaveTextContent(/seguimiento implementado o validado/);
  });

  it("requires at least two conditions for a CONTROLLED comparison", () => {
    renderSection(definition({ comparison_type: "CONTROLLED", controlled_factors: ["Formato"], variant_count: 1 }));
    expect(screen.getByText(/Condiciones declaradas \(1 de al menos 2\)/)).toHaveTextContent("pendiente");
  });

  it("always shows the mandatory non-execution disclaimer", () => {
    renderSection(definition());
    expect(
      screen.getByText(
        /Autorizar no significa que la ejecución, la asignación, la exposición, la validación del seguimiento, la medición o un resultado hayan ocurrido/,
      ),
    ).toBeInTheDocument();
  });

  it("renders the pinned configuration of the active authorization", async () => {
    const active = authorization();
    mockGet.mockResolvedValue(active);
    mockHistory.mockResolvedValue(history([active]));
    renderSection(definition());
    await waitFor(() => expect(screen.getByText("Visitante (sesión)")).toBeInTheDocument());
    expect(screen.getByText("MSC-1")).toBeInTheDocument();
    expect(screen.getByText("Condición A")).toBeInTheDocument();
    expect(screen.getByText("División 50/50 por sesión.")).toBeInTheDocument();
    expect(screen.getByText(/2 declaradas, 1 con seguimiento declarado/)).toHaveTextContent("informativo");
    expect(screen.getByText("Revocar autorización")).toBeInTheDocument();
  });

  it("shows earlier authorizations with their supersession / revocation state", async () => {
    const old = authorization({
      id: "EXA-0", active: false, revoked_at: "2026-01-02T00:00:00Z", revoked_reason: "superseded by re-authorization",
      superseded_by: "EXA-1",
    });
    const active = authorization();
    mockGet.mockResolvedValue(active);
    mockHistory.mockResolvedValue(history([old, active]));
    renderSection(definition());
    await waitFor(() => expect(screen.getByText(/reemplazada por EXA-1/)).toBeInTheDocument());
  });
});

describe("ExecutionAuthorizationSection — authorize", () => {
  it("authorizes with only the two declared fields plus the idempotency key, and rotates the key", async () => {
    mockAuthorize.mockResolvedValue(authorization());
    const { onChanged } = renderSection(definition({ is_pinned: false }), "MEMBER");
    await userEvent.click(screen.getByText("Autorizar configuración"));
    fillAuthorize();
    await userEvent.click(screen.getByText("Confirmar autorización"));
    await waitFor(() => expect(mockAuthorize).toHaveBeenCalledTimes(1));
    const [, , payload] = mockAuthorize.mock.calls[0];
    expect(payload).toEqual({
      client_request_id: "uuid-1",
      unit_of_assignment: "Visitante (sesión)",
      allocation_design: "División 50/50 por sesión.",
    });
    // The client never names the Definition, Contract or Variants.
    expect(Object.keys(payload).sort()).toEqual(["allocation_design", "client_request_id", "unit_of_assignment"]);
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("Unidad de asignación")).not.toBeInTheDocument();
  });

  it("requires both configuration fields, client-side", async () => {
    renderSection(definition(), "OWNER");
    await userEvent.click(screen.getByText("Autorizar configuración"));
    await userEvent.click(screen.getByText("Confirmar autorización"));
    expect(screen.getByRole("alert")).toHaveTextContent(/Unidad de asignación/);
    fireEvent.change(screen.getByLabelText("Unidad de asignación"), { target: { value: "Visitante" } });
    await userEvent.click(screen.getByText("Confirmar autorización"));
    expect(screen.getByRole("alert")).toHaveTextContent(/Diseño de asignación/);
    expect(mockAuthorize).not.toHaveBeenCalled();
  });

  it("keeps the form open and editable on a prerequisite rejection", async () => {
    mockAuthorize.mockRejectedValue(new ApiError(409, "EXECUTION_AUTHORIZATION_INSUFFICIENT_VARIANTS", "x"));
    renderSection(definition());
    await userEvent.click(screen.getByText("Autorizar configuración"));
    fillAuthorize();
    await userEvent.click(screen.getByText("Confirmar autorización"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/no tiene suficientes condiciones/));
    expect(screen.getByLabelText("Unidad de asignación")).toBeInTheDocument();
  });

  it("discards the draft and refetches on STRATEGY_STALE", async () => {
    mockAuthorize.mockRejectedValue(new ApiError(409, "EXECUTION_AUTHORIZATION_STRATEGY_STALE", "stale"));
    const { onChanged } = renderSection(definition());
    await userEvent.click(screen.getByText("Autorizar configuración"));
    fillAuthorize();
    await userEvent.click(screen.getByText("Confirmar autorización"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(screen.queryByLabelText("Unidad de asignación")).not.toBeInTheDocument();
  });

  it("rotates the idempotency key on IDEMPOTENCY_KEY_CONFLICT and keeps the form open", async () => {
    mockAuthorize.mockRejectedValue(new ApiError(409, "IDEMPOTENCY_KEY_CONFLICT", "conflict"));
    renderSection(definition());
    await userEvent.click(screen.getByText("Autorizar configuración"));
    fillAuthorize();
    await userEvent.click(screen.getByText("Confirmar autorización"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/no coincide con un envío anterior/));
    mockAuthorize.mockResolvedValue(authorization());
    await userEvent.click(screen.getByText("Confirmar autorización"));
    await waitFor(() => expect(mockAuthorize).toHaveBeenCalledTimes(2));
    expect(mockAuthorize.mock.calls[1][2].client_request_id).not.toBe(mockAuthorize.mock.calls[0][2].client_request_id);
  });

  it("keeps the same key across a retryable (non-conflict) failure", async () => {
    mockAuthorize.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
    renderSection(definition());
    await userEvent.click(screen.getByText("Autorizar configuración"));
    fillAuthorize();
    await userEvent.click(screen.getByText("Confirmar autorización"));
    await waitFor(() => expect(mockAuthorize).toHaveBeenCalledTimes(1));
    mockAuthorize.mockResolvedValue(authorization());
    await userEvent.click(screen.getByText("Confirmar autorización"));
    await waitFor(() => expect(mockAuthorize).toHaveBeenCalledTimes(2));
    expect(mockAuthorize.mock.calls[1][2].client_request_id).toBe(mockAuthorize.mock.calls[0][2].client_request_id);
  });
});

describe("ExecutionAuthorizationSection — revoke", () => {
  async function renderActive(role: string | null = "OWNER") {
    const active = authorization();
    mockGet.mockResolvedValue(active);
    mockHistory.mockResolvedValue(history([active]));
    const view = renderSection(definition(), role);
    await waitFor(() => expect(screen.getByText("Revocar autorización")).toBeInTheDocument());
    return view;
  }

  it("requires a reason and sends it trimmed", async () => {
    mockRevoke.mockResolvedValue(authorization({ active: false, revoked_at: "x", revoked_reason: "Error" }));
    const { onChanged } = await renderActive();
    await userEvent.click(screen.getByText("Revocar autorización"));
    await userEvent.click(screen.getByText("Confirmar revocación"));
    expect(screen.getByRole("alert")).toHaveTextContent(/motivo/);
    expect(mockRevoke).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Motivo de la revocación"), { target: { value: "  Unidad incorrecta  " } });
    await userEvent.click(screen.getByText("Confirmar revocación"));
    await waitFor(() => expect(mockRevoke).toHaveBeenCalledTimes(1));
    expect(mockRevoke.mock.calls[0][2]).toEqual({ reason: "Unidad incorrecta" });
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("explains that revocation does not erase anything that already happened", async () => {
    await renderActive();
    await userEvent.click(screen.getByText("Revocar autorización"));
    expect(screen.getByText(/No significa que una asignación, exposición o evidencia pasada no haya ocurrido/)).toBeInTheDocument();
  });

  it("refreshes the view when there is no active authorization to revoke", async () => {
    mockRevoke.mockRejectedValue(new ApiError(409, "EXECUTION_AUTHORIZATION_NONE_ACTIVE", "none"));
    await renderActive();
    await userEvent.click(screen.getByText("Revocar autorización"));
    fireEvent.change(screen.getByLabelText("Motivo de la revocación"), { target: { value: "x" } });
    await userEvent.click(screen.getByText("Confirmar revocación"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/No hay una autorización activa/));
    expect(screen.queryByLabelText("Motivo de la revocación")).not.toBeInTheDocument();
  });
});

describe("ExecutionAuthorizationSection — authority and negative surface", () => {
  it("hides Authorize and Revoke from a role below MEMBER", async () => {
    const active = authorization();
    mockGet.mockResolvedValue(active);
    mockHistory.mockResolvedValue(history([active]));
    renderSection(definition(), "VIEWER");
    await waitFor(() => expect(screen.getByText("Visitante (sesión)")).toBeInTheDocument());
    expect(screen.queryByText("Autorizar configuración")).not.toBeInTheDocument();
    expect(screen.queryByText("Volver a autorizar configuración")).not.toBeInTheDocument();
    expect(screen.queryByText("Revocar autorización")).not.toBeInTheDocument();
  });

  it("exposes no execute, assign, expose, tracking-validation, result or winner control", async () => {
    const active = authorization();
    mockGet.mockResolvedValue(active);
    mockHistory.mockResolvedValue(history([active]));
    renderSection(definition());
    await waitFor(() => expect(screen.getByText("Revocar autorización")).toBeInTheDocument());
    for (const forbidden of [
      /ejecutar/i, /iniciar/i, /asignar unidades/i, /exponer/i, /validar seguimiento/i, /resultado/i, /ganador/i,
      /autorizar ejecución/i,
    ]) {
      expect(screen.queryByRole("button", { name: forbidden })).not.toBeInTheDocument();
    }
  });

  it("never renders a validity, readiness or execution-started label", async () => {
    const active = authorization();
    mockGet.mockResolvedValue(active);
    mockHistory.mockResolvedValue(history([active]));
    const { container } = render(
      <ExecutionAuthorizationSection campaignId="c" experiment={EXPERIMENT} definition={definition()} role="OWNER" onChanged={vi.fn()} />,
    );
    await waitFor(() => expect(screen.getAllByText("Visitante (sesión)").length).toBeGreaterThan(0));
    const text = container.textContent ?? "";
    for (const forbidden of [/experimento válido/i, /causalmente/i, /medición lista/i, /seguimiento listo/i, /ejecución iniciada/i]) {
      expect(text).not.toMatch(forbidden);
    }
  });
});
