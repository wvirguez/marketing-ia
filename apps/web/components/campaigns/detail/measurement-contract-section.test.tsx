import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MeasurementContractSection } from "@/components/campaigns/detail/measurement-contract-section";
import { ApiError } from "@/lib/api/client";
import type { ExperimentDefinitionPublic, ExperimentPublic, MeasurementContractPublic } from "@/types/strategy";

vi.mock("@/lib/api/strategy", () => ({
  declareMeasurementContract: vi.fn(),
  getMeasurementContract: vi.fn(),
}));

import { declareMeasurementContract, getMeasurementContract } from "@/lib/api/strategy";

const mockDeclare = vi.mocked(declareMeasurementContract);
const mockGet = vi.mocked(getMeasurementContract);

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
    variant_count: 0,
    is_pinned: false,
    has_measurement_contract: false,
    measurement_contract_version: null,
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

function contract(overrides: Partial<MeasurementContractPublic> = {}): MeasurementContractPublic {
  return {
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
        id: "RSG-1", ordinal: 1, name: "CTR", description: "Click-through rate.",
        expected_direction: null, evidence_requirement: null, tracking_required: false,
        bound_metric_name: null, channel_binding: null, bound_channel: null, min_data_points: null,
      },
    ],
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderSection(def: ExperimentDefinitionPublic, role: string | null = "OWNER") {
  const onChanged = vi.fn();
  render(
    <MeasurementContractSection campaignId="campaign-1" experiment={EXPERIMENT} definition={def} role={role} onChanged={onChanged} />,
  );
  return { onChanged };
}

function fillFirstSignal(name = "CTR", description = "Click-through rate.") {
  fireEvent.change(screen.getByLabelText("Nombre"), { target: { value: name } });
  fireEvent.change(screen.getByLabelText("Descripción"), { target: { value: description } });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockResolvedValue(null);
  uuidCounter = 0;
  vi.spyOn(crypto, "randomUUID").mockImplementation(() => `uuid-${++uuidCounter}` as ReturnType<typeof crypto.randomUUID>);
});

describe("MeasurementContractSection — no-contract state", () => {
  it("shows the no-contract state and never fetches when none is declared", async () => {
    renderSection(definition({ has_measurement_contract: false }));
    expect(screen.getByText("No se ha declarado un contrato de medición para esta versión de la definición.")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("shows a declare control for MEMBER+", () => {
    renderSection(definition(), "MEMBER");
    expect(screen.getByText("Declarar contrato de medición")).toBeInTheDocument();
  });
});

describe("MeasurementContractSection — reading the current tip", () => {
  it("fetches and renders the current tip when the definition reports one", async () => {
    mockGet.mockResolvedValue(contract({ version: 2, measurement_window_days: 14, success_criterion: "CTR improves." }));
    renderSection(definition({ has_measurement_contract: true, measurement_contract_version: 2 }));
    await waitFor(() => expect(screen.getByText("14 días")).toBeInTheDocument());
    expect(screen.getByText("CTR improves.")).toBeInTheDocument();
    expect(screen.getByText("CTR")).toBeInTheDocument();
    expect(screen.getByText("Revisar contrato de medición")).toBeInTheDocument();
  });
});

describe("MeasurementContractSection — declaration", () => {
  it("declares a new contract with a default signal row and rotates the key on success", async () => {
    mockDeclare.mockResolvedValue(contract());
    const { onChanged } = renderSection(definition(), "OWNER");
    await userEvent.click(screen.getByText("Declarar contrato de medición"));
    fillFirstSignal();
    await userEvent.click(screen.getByText("Confirmar contrato"));
    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(1));
    const [, , payload] = mockDeclare.mock.calls[0];
    expect(payload.client_request_id).toBe("uuid-1");
    expect(payload.definition_version_id).toBe("EXD-1");
    expect(payload.base_version).toBe(0);
    expect(payload.signals).toEqual([
      { name: "CTR", description: "Click-through rate.", expected_direction: null, evidence_requirement: null, tracking_required: false },
    ]);
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("Nombre")).not.toBeInTheDocument();
  });

  it("requires at least one signal name and description", async () => {
    renderSection(definition());
    await userEvent.click(screen.getByText("Declarar contrato de medición"));
    await userEvent.click(screen.getByText("Confirmar contrato"));
    expect(screen.getByRole("alert")).toHaveTextContent(/Nombre/);
    expect(mockDeclare).not.toHaveBeenCalled();
  });

  it("rejects duplicate normalized signal names", async () => {
    renderSection(definition());
    await userEvent.click(screen.getByText("Declarar contrato de medición"));
    fillFirstSignal("CTR", "d1");
    await userEvent.click(screen.getByText("Agregar señal"));
    const names = screen.getAllByLabelText("Nombre");
    const descriptions = screen.getAllByLabelText("Descripción");
    await userEvent.type(names[1], "  ctr  ");
    await userEvent.type(descriptions[1], "d2");
    await userEvent.click(screen.getByText("Confirmar contrato"));
    expect(screen.getByRole("alert")).toHaveTextContent(/no pueden repetirse/);
    expect(mockDeclare).not.toHaveBeenCalled();
  });

  it("can add and remove signal rows", async () => {
    renderSection(definition());
    await userEvent.click(screen.getByText("Declarar contrato de medición"));
    expect(screen.getAllByLabelText("Nombre")).toHaveLength(1);
    await userEvent.click(screen.getByText("Agregar señal"));
    expect(screen.getAllByLabelText("Nombre")).toHaveLength(2);
    await userEvent.click(screen.getAllByText("Quitar señal")[0]);
    expect(screen.getAllByLabelText("Nombre")).toHaveLength(1);
  });

  it("requires a success criterion for a CONTROLLED comparison, client-side", async () => {
    renderSection(definition({ comparison_type: "CONTROLLED", controlled_factors: ["Format"] }));
    await userEvent.click(screen.getByText("Declarar contrato de medición"));
    fillFirstSignal();
    await userEvent.click(screen.getByText("Confirmar contrato"));
    expect(screen.getByRole("alert")).toHaveTextContent(/criterio de éxito/);
    expect(mockDeclare).not.toHaveBeenCalled();
  });
});

describe("MeasurementContractSection — revision", () => {
  it("preloads the current tip and sends base_version = the current tip version", async () => {
    mockGet.mockResolvedValue(contract({ version: 3, signals: [
      { id: "RSG-1", ordinal: 1, name: "CTR", description: "d", expected_direction: "INCREASE", evidence_requirement: null, tracking_required: true, bound_metric_name: null, channel_binding: null, bound_channel: null, min_data_points: null },
    ] }));
    mockDeclare.mockResolvedValue(contract({ version: 4 }));
    renderSection(definition({ has_measurement_contract: true, measurement_contract_version: 3 }));
    await waitFor(() => expect(screen.getByText("Revisar contrato de medición")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Revisar contrato de medición"));
    expect((screen.getByLabelText("Nombre") as HTMLInputElement).value).toBe("CTR");
    await userEvent.click(screen.getByText("Confirmar revisión"));
    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(1));
    expect(mockDeclare.mock.calls[0][2].base_version).toBe(3);
  });
});

describe("MeasurementContractSection — error handling", () => {
  it("keeps the form open and editable on a material rejection (UNCHANGED)", async () => {
    mockDeclare.mockRejectedValue(new ApiError(409, "MEASUREMENT_CONTRACT_UNCHANGED", "unchanged"));
    renderSection(definition());
    await userEvent.click(screen.getByText("Declarar contrato de medición"));
    fillFirstSignal();
    await userEvent.click(screen.getByText("Confirmar contrato"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/No hay cambios/));
    expect(screen.getByLabelText("Nombre")).toBeInTheDocument(); // form stays open
  });

  it("discards the draft and refetches on DEFINITION_VERSION_NOT_CURRENT", async () => {
    mockDeclare.mockRejectedValue(
      new ApiError(409, "MEASUREMENT_CONTRACT_DEFINITION_VERSION_NOT_CURRENT", "stale"),
    );
    const { onChanged } = renderSection(definition());
    await userEvent.click(screen.getByText("Declarar contrato de medición"));
    fillFirstSignal();
    await userEvent.click(screen.getByText("Confirmar contrato"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(screen.queryByLabelText("Nombre")).not.toBeInTheDocument();
  });

  it("rotates the idempotency key on IDEMPOTENCY_KEY_CONFLICT and keeps the form open", async () => {
    mockDeclare.mockRejectedValue(new ApiError(409, "IDEMPOTENCY_KEY_CONFLICT", "conflict"));
    renderSection(definition());
    await userEvent.click(screen.getByText("Declarar contrato de medición"));
    fillFirstSignal();
    await userEvent.click(screen.getByText("Confirmar contrato"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/no coincide con un envío anterior/));
    mockDeclare.mockResolvedValue(contract());
    await userEvent.click(screen.getByText("Confirmar contrato"));
    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(2));
    expect(mockDeclare.mock.calls[1][2].client_request_id).not.toBe(mockDeclare.mock.calls[0][2].client_request_id);
  });
});

describe("MeasurementContractSection — negative surface", () => {
  it("exposes no allocation, exposure, execution, result, or winner control", () => {
    renderSection(definition());
    for (const forbidden of [/asignar/i, /exponer/i, /autorizar ejecución/i, /ganador/i, /resultado/i]) {
      expect(screen.queryByRole("button", { name: forbidden })).not.toBeInTheDocument();
    }
  });
});

describe("MeasurementContractSection — Governed Execution Start freeze copy", () => {
  it("explains that a started execution freezes the contract permanently, even after revocation", async () => {
    mockDeclare.mockRejectedValue(new ApiError(409, "MEASUREMENT_CONTRACT_FROZEN_BY_EXECUTION_START", "frozen"));
    renderSection(definition());
    await userEvent.click(screen.getByText("Declarar contrato de medición"));
    fillFirstSignal();
    await userEvent.click(screen.getByText("Confirmar contrato"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/congelado de forma permanente/));
    expect(screen.getByRole("alert")).toHaveTextContent(/nuevo experimento/);
  });
});

describe("MeasurementContractSection — Pre-Execution Measurement Declaration", () => {
  async function openStructured(mode: "DESCRIPTIVE" | "COMPARATIVE", def = definition()) {
    renderSection(def);
    await userEvent.click(screen.getByText("Declarar contrato de medición"));
    await userEvent.selectOptions(screen.getByLabelText("Tipo de declaración de medición"), mode);
    fillFirstSignal();
  }

  function fillBinding(index = 0, { metric = "clicks", binding = "ANY", channel = "", min = "3" } = {}) {
    fireEvent.change(screen.getByLabelText("Métrica vinculada"), { target: { value: metric } });
    fireEvent.change(screen.getByLabelText("Canal"), { target: { value: binding } });
    if (binding === "EXACT") {
      fireEvent.change(screen.getByLabelText("Nombre exacto del canal"), { target: { value: channel } });
    }
    if (screen.queryByLabelText("Mínimo de datos")) {
      fireEvent.change(screen.getByLabelText("Mínimo de datos"), { target: { value: min } });
    }
    void index;
  }

  it("keeps the LEGACY mode by default and sends none of the structured fields", async () => {
    mockDeclare.mockResolvedValue(contract());
    renderSection(definition());
    await userEvent.click(screen.getByText("Declarar contrato de medición"));
    expect((screen.getByLabelText("Tipo de declaración de medición") as HTMLSelectElement).value).toBe("LEGACY");
    expect(screen.queryByLabelText("Métrica vinculada")).not.toBeInTheDocument();
    expect(screen.queryByTestId("declaration-explanation")).not.toBeInTheDocument();
    fillFirstSignal();
    await userEvent.click(screen.getByText("Confirmar contrato"));
    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(1));
    const payload = mockDeclare.mock.calls[0][2];
    expect(payload).not.toHaveProperty("declaration_level");
    expect(payload).not.toHaveProperty("baseline_window_days");
    expect(payload.signals[0]).not.toHaveProperty("bound_metric_name");
  });

  it("sends a structured DESCRIPTIVE declaration with per-signal binding and min data points", async () => {
    mockDeclare.mockResolvedValue(contract({ declaration_level: "DESCRIPTIVE", declaration_semantics_version: 1 }));
    await openStructured("DESCRIPTIVE");
    fireEvent.change(screen.getByLabelText(/Ventana de medición/), { target: { value: "14" } });
    expect(screen.queryByLabelText(/Ventana base/)).not.toBeInTheDocument();
    fillBinding(0, { metric: "  clicks ", binding: "EXACT", channel: "Instagram", min: "3" });
    await userEvent.click(screen.getByText("Confirmar contrato"));
    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(1));
    const payload = mockDeclare.mock.calls[0][2];
    expect(payload).toMatchObject({
      declaration_level: "DESCRIPTIVE",
      declaration_semantics_version: 1,
      baseline_window_days: null,
      measurement_window_days: 14,
    });
    expect(payload.signals[0]).toMatchObject({
      bound_metric_name: "clicks",
      channel_binding: "EXACT",
      bound_channel: "Instagram",
      min_data_points: 3,
    });
  });

  it("sends a structured COMPARATIVE declaration with a baseline and no min data points", async () => {
    mockDeclare.mockResolvedValue(contract({ declaration_level: "COMPARATIVE", declaration_semantics_version: 1 }));
    await openStructured("COMPARATIVE");
    fireEvent.change(screen.getByLabelText(/Ventana de medición/), { target: { value: "14" } });
    fireEvent.change(screen.getByLabelText(/Ventana base/), { target: { value: "7" } });
    expect(screen.queryByLabelText("Mínimo de datos")).not.toBeInTheDocument();
    fillBinding(0, { metric: "clicks", binding: "ANY" });
    await userEvent.click(screen.getByText("Confirmar contrato"));
    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(1));
    const payload = mockDeclare.mock.calls[0][2];
    expect(payload).toMatchObject({ declaration_level: "COMPARATIVE", baseline_window_days: 7 });
    expect(payload.signals[0]).toMatchObject({ channel_binding: "ANY", bound_channel: null, min_data_points: null });
  });

  it.each([
    ["a window below the structured minimum", "3", "clicks", "ANY", "", "3", /entre 4 y 3650/],
    ["a missing metric", "14", "", "ANY", "", "3", /Métrica vinculada/],
    ["an EXACT binding without a channel", "14", "clicks", "EXACT", "", "3", /Canal/],
    ["a missing min data points", "14", "clicks", "ANY", "", "", /Mínimo de datos/],
  ])("rejects %s client-side without calling the API", async (_label, window, metric, binding, channel, min, message) => {
    await openStructured("DESCRIPTIVE");
    fireEvent.change(screen.getByLabelText(/Ventana de medición/), { target: { value: window } });
    fillBinding(0, { metric, binding, channel, min });
    await userEvent.click(screen.getByText("Confirmar contrato"));
    expect(screen.getByRole("alert")).toHaveTextContent(message);
    expect(mockDeclare).not.toHaveBeenCalled();
  });

  it("rejects a comparative declaration without a valid baseline", async () => {
    await openStructured("COMPARATIVE");
    fireEvent.change(screen.getByLabelText(/Ventana de medición/), { target: { value: "14" } });
    fireEvent.change(screen.getByLabelText(/Ventana base/), { target: { value: "2" } });
    fillBinding(0);
    await userEvent.click(screen.getByText("Confirmar contrato"));
    expect(screen.getByRole("alert")).toHaveTextContent(/ventana base/);
    expect(mockDeclare).not.toHaveBeenCalled();
  });

  it("rejects ANY-and-EXACT for one metric and a repeated slot", async () => {
    await openStructured("DESCRIPTIVE");
    fireEvent.change(screen.getByLabelText(/Ventana de medición/), { target: { value: "14" } });
    fillBinding(0, { metric: "clicks", binding: "ANY", min: "1" });
    await userEvent.click(screen.getByText("Agregar señal"));
    fireEvent.change(screen.getAllByLabelText("Nombre")[1], { target: { value: "Other" } });
    fireEvent.change(screen.getAllByLabelText("Descripción")[1], { target: { value: "d" } });
    fireEvent.change(screen.getAllByLabelText("Métrica vinculada")[1], { target: { value: "clicks" } });
    fireEvent.change(screen.getAllByLabelText("Canal")[1], { target: { value: "EXACT" } });
    fireEvent.change(screen.getByLabelText("Nombre exacto del canal"), { target: { value: "email" } });
    fireEvent.change(screen.getAllByLabelText("Mínimo de datos")[1], { target: { value: "1" } });
    await userEvent.click(screen.getByText("Confirmar contrato"));
    expect(screen.getByRole("alert")).toHaveTextContent(/cualquier canal y a un canal exacto/);
    expect(mockDeclare).not.toHaveBeenCalled();
  });

  it("disables the structured modes for a CONTROLLED comparison and says why", async () => {
    renderSection(definition({ comparison_type: "CONTROLLED" }));
    await userEvent.click(screen.getByText("Declarar contrato de medición"));
    expect((screen.getByRole("option", { name: /descriptiva/i }) as HTMLOptionElement).disabled).toBe(true);
    expect((screen.getByRole("option", { name: /comparativa/i }) as HTMLOptionElement).disabled).toBe(true);
    expect(screen.getByText(/no están disponibles para comparaciones controladas/)).toBeInTheDocument();
  });

  it("explains the frozen semantics in the structured form and never claims validation or results", async () => {
    await openStructured("COMPARATIVE");
    const explanation = screen.getByTestId("declaration-explanation");
    for (const expected of [
      /exacta y distinguiendo mayúsculas/,
      /24 horas transcurridas/,
      /mínimo estructurado es de 4 días/,
      /ambiguas/,
      /excluidas por la\s+medición futura/,
      /cada canal se evaluará por separado/,
      /un dato por lado/,
      /congelada de forma permanente/,
      /no valida ni prueba la evidencia/,
    ]) {
      expect(explanation).toHaveTextContent(expected);
    }
    for (const forbidden of [/elegible/i, /validad[ao]s? por/i, /exitos[oa]/i, /ganador/i]) {
      expect(explanation).not.toHaveTextContent(forbidden);
    }
  });

  it("renders the stored structured declaration and each signal binding", async () => {
    mockGet.mockResolvedValue(
      contract({
        version: 2,
        measurement_window_days: 14,
        baseline_window_days: 7,
        declaration_level: "COMPARATIVE",
        declaration_semantics_version: 1,
        signals: [
          {
            id: "RSG-1", ordinal: 1, name: "CTR", description: "d", expected_direction: null, evidence_requirement: null,
            tracking_required: false, bound_metric_name: "clicks", channel_binding: "EXACT", bound_channel: "Instagram",
            min_data_points: null,
          },
          {
            id: "RSG-2", ordinal: 2, name: "Reach", description: "d", expected_direction: null, evidence_requirement: null,
            tracking_required: false, bound_metric_name: "reach", channel_binding: "ANY", bound_channel: null,
            min_data_points: 2,
          },
        ],
      }),
    );
    renderSection(definition({ has_measurement_contract: true, measurement_contract_version: 2 }));
    await waitFor(() => expect(screen.getByText(/Declaración comparativa · semántica versión 1/)).toBeInTheDocument());
    expect(screen.getByText("7 días")).toBeInTheDocument();
    expect(screen.getByText(/Métrica declarada «clicks» · canal exacto «Instagram»/)).toBeInTheDocument();
    expect(screen.getByText(/Métrica declarada «reach» · cualquier canal · mínimo de datos: 2/)).toBeInTheDocument();
  });

  it("marks a legacy contract as unstructured", async () => {
    mockGet.mockResolvedValue(contract({ version: 1 }));
    renderSection(definition({ has_measurement_contract: true, measurement_contract_version: 1 }));
    await waitFor(() => expect(screen.getByText(/Sin declaración estructurada \(heredado\)/)).toBeInTheDocument());
  });

  it("preloads a structured tip into the form for a revision", async () => {
    mockGet.mockResolvedValue(
      contract({
        version: 1, measurement_window_days: 14, declaration_level: "DESCRIPTIVE", declaration_semantics_version: 1,
        signals: [
          {
            id: "RSG-1", ordinal: 1, name: "CTR", description: "d", expected_direction: null, evidence_requirement: null,
            tracking_required: false, bound_metric_name: "clicks", channel_binding: "ANY", bound_channel: null, min_data_points: 4,
          },
        ],
      }),
    );
    renderSection(definition({ has_measurement_contract: true, measurement_contract_version: 1 }));
    await waitFor(() => expect(screen.getByText("Revisar contrato de medición")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Revisar contrato de medición"));
    expect((screen.getByLabelText("Tipo de declaración de medición") as HTMLSelectElement).value).toBe("DESCRIPTIVE");
    expect((screen.getByLabelText("Métrica vinculada") as HTMLInputElement).value).toBe("clicks");
    expect((screen.getByLabelText("Mínimo de datos") as HTMLInputElement).value).toBe("4");
  });

  it.each([
    ["MEASUREMENT_CONTRACT_DECLARATION_INVALID", /incompleta o es inconsistente/],
    ["MEASUREMENT_CONTRACT_CONTROLLED_DECLARATION_NOT_SUPPORTED", /no están disponibles para comparaciones controladas/],
    ["MEASUREMENT_CONTRACT_BINDING_CONFLICT", /vínculos de métrica y canal en conflicto/],
  ])("describes the typed rejection %s", async (code, message) => {
    mockDeclare.mockRejectedValue(new ApiError(422, code, "x"));
    await openStructured("DESCRIPTIVE");
    fireEvent.change(screen.getByLabelText(/Ventana de medición/), { target: { value: "14" } });
    fillBinding(0);
    await userEvent.click(screen.getByText("Confirmar contrato"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(message));
  });
});
