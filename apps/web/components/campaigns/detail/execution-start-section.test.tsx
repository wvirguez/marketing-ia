import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExecutionAuthorizationSection } from "@/components/campaigns/detail/execution-authorization-section";
import { ApiError } from "@/lib/api/client";
import type {
  ExecutionAuthorizationHistoryResponse,
  ExecutionAuthorizationPublic,
  ExperimentDefinitionPublic,
  ExperimentPublic,
} from "@/types/strategy";

// Governed Execution Start — component tests for the start mode inside the existing Execution Authorization
// section. These are COMPONENT tests with a mocked API client; they are NOT browser validation.

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
  getExecutionAuthorization,
  getExecutionAuthorizationHistory,
  startExecution,
  listEvidenceClaims,
} from "@/lib/api/strategy";

const mockGet = vi.mocked(getExecutionAuthorization);
const mockHistory = vi.mocked(getExecutionAuthorizationHistory);
const mockStart = vi.mocked(startExecution);

let uuidCounter = 0;

const DEFINITION: ExperimentDefinitionPublic = {
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
};

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
    declaration_level: null,
    declaration_semantics_version: null,
    measurement_window_days: null,
    baseline_window_days: null,
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

function renderSection(role: string | null = "OWNER") {
  const onChanged = vi.fn();
  const view = render(
    <ExecutionAuthorizationSection
      campaignId="campaign-1"
      experiment={EXPERIMENT}
      definition={DEFINITION}
      role={role}
      onChanged={onChanged}
    />,
  );
  return { onChanged, ...view };
}

async function withActive(active: ExecutionAuthorizationPublic, all: ExecutionAuthorizationPublic[] = [active]) {
  mockGet.mockResolvedValue(active);
  mockHistory.mockResolvedValue(history(all));
}

async function openStart() {
  await userEvent.click(await screen.findByText("Registrar inicio de ejecución"));
}

function setInstant(value: string) {
  fireEvent.change(screen.getByLabelText("Inicio atestiguado (fecha y hora)"), { target: { value } });
}

async function confirmAndSubmit() {
  await userEvent.click(screen.getByLabelText(/Entiendo que es una declaración mía/));
  await userEvent.click(screen.getByText("Confirmar inicio declarado"));
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listEvidenceClaims).mockResolvedValue({ experiment_id: "EXP-1", start_id: "", claims: [] });
  mockGet.mockResolvedValue(null);
  mockHistory.mockResolvedValue(history([]));
  uuidCounter = 0;
  vi.spyOn(crypto, "randomUUID").mockImplementation(() => `uuid-${++uuidCounter}` as ReturnType<typeof crypto.randomUUID>);
});

describe("Execution Start — availability", () => {
  it("offers the start action to MEMBER+ on an active, unstarted authorization", async () => {
    await withActive(authorization());
    renderSection("MEMBER");
    expect(await screen.findByText("Registrar inicio de ejecución")).toBeInTheDocument();
  });

  it("offers no start action to a non-member role or when there is no active authorization", async () => {
    await withActive(authorization());
    const { unmount } = renderSection("VIEWER");
    await screen.findByText(/Autorización de ejecución/);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(screen.queryByText("Registrar inicio de ejecución")).not.toBeInTheDocument();
    unmount();
    mockGet.mockResolvedValue(null);
    renderSection("OWNER");
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("Registrar inicio de ejecución")).not.toBeInTheDocument();
  });
});

describe("Execution Start — confirmation and warning", () => {
  it("states that it is a human declaration, cannot be corrected and permanently freezes contract and conditions", async () => {
    await withActive(authorization());
    renderSection();
    await openStart();
    const panel = screen.getByText(/Vas a declarar, como persona/).closest(".panel") as HTMLElement;
    expect(panel).toHaveTextContent(/el sistema no la verifica externamente/);
    expect(panel).toHaveTextContent(/No se puede corregir/);
    expect(panel).toHaveTextContent(/contrato de medición/);
    expect(panel).toHaveTextContent(/declaración de nuevas condiciones/);
    expect(panel).toHaveTextContent(/nuevo experimento/);
  });

  it("requires an explicit confirmation before calling the API", async () => {
    await withActive(authorization());
    renderSection();
    await openStart();
    await userEvent.click(screen.getByText("Confirmar inicio declarado"));
    expect(screen.getByRole("alert")).toHaveTextContent(/Debes confirmar/);
    expect(mockStart).not.toHaveBeenCalled();
  });

  it("defaults the attested instant to a value that is never before the authorization creation", async () => {
    await withActive(authorization({ created_at: "2999-01-01T00:00:00Z" }));
    renderSection();
    await openStart();
    const input = screen.getByLabelText("Inicio atestiguado (fecha y hora)") as HTMLInputElement;
    expect(input.type).toBe("datetime-local");
    expect(input.value).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/); // the input normalizes zero seconds away
    expect(new Date(input.value).getTime()).toBeGreaterThanOrEqual(new Date("2999-01-01T00:00:00Z").getTime());
  });

  it("rejects an empty instant without calling the API", async () => {
    await withActive(authorization());
    renderSection();
    await openStart();
    setInstant("");
    await confirmAndSubmit();
    expect(screen.getByRole("alert")).toHaveTextContent(/Indica la fecha y hora/);
    expect(mockStart).not.toHaveBeenCalled();
  });
});

describe("Execution Start — submit", () => {
  it("sends the explicit authorization id and an offset-aware ISO instant, then refreshes", async () => {
    await withActive(authorization());
    mockStart.mockResolvedValue(authorization());
    const { onChanged } = renderSection();
    await openStart();
    setInstant("2026-03-01T10:30:00");
    await confirmAndSubmit();
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1));
    const [campaign, experiment, authorizationId, payload] = mockStart.mock.calls[0];
    expect([campaign, experiment, authorizationId]).toEqual(["campaign-1", "EXP-1", "EXA-1"]);
    expect(Object.keys(payload).sort()).toEqual(["client_request_id", "started_at"]);
    expect(payload.started_at).toBe(new Date("2026-03-01T10:30:00").toISOString());
    expect(payload.started_at.endsWith("Z")).toBe(true);
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(screen.queryByLabelText("Inicio atestiguado (fecha y hora)")).not.toBeInTheDocument();
  });

  it("keeps the same key when retrying the same instant and rotates it when the instant changes", async () => {
    await withActive(authorization());
    mockStart
      .mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR", "offline"))
      .mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR", "offline"))
      .mockResolvedValueOnce(authorization());
    renderSection();
    await openStart();
    setInstant("2026-03-01T10:30:00");
    await confirmAndSubmit();
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByText("Confirmar inicio declarado"));
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(2));
    expect(mockStart.mock.calls[1][3].client_request_id).toBe(mockStart.mock.calls[0][3].client_request_id);
    setInstant("2026-03-01T10:45:00");
    await userEvent.click(screen.getByText("Confirmar inicio declarado"));
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(3));
    expect(mockStart.mock.calls[2][3].client_request_id).not.toBe(mockStart.mock.calls[0][3].client_request_id);
  });

  it("rotates the key after an IDEMPOTENCY_KEY_CONFLICT and keeps the form open", async () => {
    await withActive(authorization());
    mockStart
      .mockRejectedValueOnce(new ApiError(409, "IDEMPOTENCY_KEY_CONFLICT", "conflict"))
      .mockResolvedValueOnce(authorization());
    renderSection();
    await openStart();
    setInstant("2026-03-01T10:30:00");
    await confirmAndSubmit();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/no coincide con un envío anterior/));
    await userEvent.click(screen.getByText("Confirmar inicio declarado"));
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(2));
    expect(mockStart.mock.calls[1][3].client_request_id).not.toBe(mockStart.mock.calls[0][3].client_request_id);
  });
});

describe("Execution Start — error copy", () => {
  async function failWith(code: string, status = 409) {
    await withActive(authorization());
    mockStart.mockRejectedValue(new ApiError(status, code, "x"));
    const view = renderSection();
    await openStart();
    setInstant("2026-03-01T10:30:00");
    await confirmAndSubmit();
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1));
    return view;
  }

  it("keeps the form open on an invalid start time", async () => {
    await failWith("EXECUTION_START_TIME_INVALID", 422);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/no son admisibles/));
    expect(screen.getByLabelText("Inicio atestiguado (fecha y hora)")).toBeInTheDocument();
  });

  it.each([
    ["EXECUTION_START_AUTHORIZATION_NOT_ACTIVE", /ya no está activa/],
    ["EXECUTION_START_ALREADY_STARTED", /ya tiene un inicio atestiguado/],
    ["EXECUTION_START_AUTHORIZATION_STALE", /Autoriza de nuevo/],
  ])("discards the draft and refetches on %s", async (code, message) => {
    const { onChanged } = await failWith(code);
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(message));
    expect(screen.queryByLabelText("Inicio atestiguado (fecha y hora)")).not.toBeInTheDocument();
  });
});

describe("Execution Start — started state", () => {
  const started = authorization({
    execution_start: {
      id: "EXS-1",
      started_at: "2026-03-01T10:30:00Z",
      created_at: "2026-03-01T10:36:00Z",
    },
  });

  it("shows the attested instant and the server record time separately, labelled as a human declaration", async () => {
    await withActive(started);
    renderSection();
    expect(await screen.findByText("Inicio atestiguado por una persona")).toBeInTheDocument();
    expect(screen.getByText("Registrado en el sistema")).toBeInTheDocument();
    expect(screen.getByText(new Date("2026-03-01T10:30:00Z").toLocaleString("es"))).toBeInTheDocument();
    expect(screen.getByText(new Date("2026-03-01T10:36:00Z").toLocaleString("es"))).toBeInTheDocument();
    expect(screen.getByText(/el sistema no verifica externamente que la ejecución haya comenzado/)).toBeInTheDocument();
    expect(screen.getByText(/con inicio atestiguado/)).toBeInTheDocument();
  });

  it("hides the start action, disables reauthorization with an explanation, and keeps revocation", async () => {
    await withActive(started);
    renderSection();
    await screen.findByText("Inicio atestiguado por una persona");
    expect(screen.queryByText("Registrar inicio de ejecución")).not.toBeInTheDocument();
    const reauthorize = screen.getByText("Volver a autorizar configuración") as HTMLButtonElement;
    expect(reauthorize).toBeDisabled();
    expect(reauthorize).toHaveAccessibleDescription(/revócala explícitamente antes de autorizar de nuevo/);
    expect(screen.getByText("Revocar autorización")).not.toBeDisabled();
  });

  it("lists a revoked started authorization in the history with its attested and recorded times", async () => {
    const revoked = authorization({
      id: "EXA-0",
      active: false,
      revoked_at: "2026-03-02T00:00:00Z",
      revoked_reason: "Detenido por el equipo.",
      execution_start: started.execution_start,
    });
    await withActive(authorization({ id: "EXA-2" }), [revoked, authorization({ id: "EXA-2" })]);
    renderSection();
    const list = await screen.findByText("Autorizaciones anteriores");
    const item = list.closest("div")!.querySelector("li")!;
    expect(item).toHaveTextContent(/EXA-0/);
    expect(item).toHaveTextContent(/inicio atestiguado/);
    expect(item).toHaveTextContent(/registrado/);
  });

  it("explains that the freeze survives revocation in the revoke panel", async () => {
    await withActive(started);
    renderSection();
    await userEvent.click(await screen.findByText("Revocar autorización"));
    expect(screen.getByText(/el congelamiento del contrato y de las condiciones permanece/)).toBeInTheDocument();
  });
});

// The Evidence Claims section renders beneath a started attempt and legitimately carries NEGATED wording
// ("no es … validación"); it has its own dedicated firewall tests (evidence-claims-section.test.tsx). This
// firewall guards the Execution Start / Authorization copy, so the scan excludes that separate section.
function startSectionText(container: HTMLElement): string {
  const clone = container.cloneNode(true) as HTMLElement;
  clone.querySelectorAll('[data-testid="evidence-claims-section"]').forEach((node) => node.remove());
  return clone.textContent ?? "";
}

describe("Execution Start — language firewall", () => {
  const FORBIDDEN_CLAIMS = [/en ejecución/i, /ejecución verificada/i, /ganador/i, /éxito/i, /válid/i, /ejecutándose/i];
  const FORBIDDEN_CONTROLS = /asign(ar|ación)\b.*(unidad|celda)|exponer|exposición|resultado|ganador|detener|finalizar|completar|pausar|reanudar/i;

  it("never claims an execution, validity, success, winner or verified state, and adds no forbidden control", async () => {
    const started = authorization({
      execution_start: { id: "EXS-1", started_at: "2026-03-01T10:30:00Z", created_at: "2026-03-01T10:36:00Z" },
    });
    await withActive(started);
    const { container } = renderSection();
    await screen.findByText("Inicio atestiguado por una persona");
    for (const claim of FORBIDDEN_CLAIMS) expect(startSectionText(container)).not.toMatch(claim);
    for (const button of screen.getAllByRole("button")) expect(button.textContent ?? "").not.toMatch(FORBIDDEN_CONTROLS);
  });

  it("keeps the start panel free of positive execution claims", async () => {
    await withActive(authorization());
    const { container } = renderSection();
    await openStart();
    for (const claim of FORBIDDEN_CLAIMS) expect(container.textContent ?? "").not.toMatch(claim);
    const panel = screen.getByText(/Vas a declarar, como persona/).closest(".panel") as HTMLElement;
    expect(within(panel).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Confirmar inicio declarado",
      "Cancelar",
    ]);
  });
});
