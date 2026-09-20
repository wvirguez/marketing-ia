import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  ACKNOWLEDGEMENT_COPY,
  ExperimentVariantsSection,
} from "@/components/campaigns/detail/experiment-variants-section";
import { ApiError } from "@/lib/api/client";
import type { ExperimentDefinitionPublic, ExperimentPublic, VariantListResponse, VariantPublic } from "@/types/strategy";

vi.mock("@/lib/api/strategy", () => ({
  declareVariant: vi.fn(),
  listVariants: vi.fn(),
}));

import { declareVariant, listVariants } from "@/lib/api/strategy";

const mockDeclare = vi.mocked(declareVariant);
const mockList = vi.mocked(listVariants);

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

function variant(ordinal: number, overrides: Partial<VariantPublic> = {}): VariantPublic {
  return {
    id: `VAR-${ordinal}`,
    experiment_id: "EXP-1",
    definition_version_id: "EXD-1",
    ordinal,
    label: `Condición ${ordinal}`,
    condition_description: `Descripción ${ordinal}`,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function page(items: VariantPublic[], total = items.length, offset = 0): VariantListResponse {
  return { experiment_id: "EXP-1", items, limit: 100, offset, total };
}

function renderSection(
  def: ExperimentDefinitionPublic,
  { role = "OWNER", onChanged = vi.fn() }: { role?: string | null; onChanged?: () => void } = {},
) {
  render(
    <ExperimentVariantsSection
      campaignId="campaign-1"
      experiment={{ ...EXPERIMENT, definition: def }}
      definition={def}
      role={role}
      onChanged={onChanged}
    />,
  );
  return { onChanged };
}

function set(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

async function openAndFill(overrides: { label?: string; description?: string; acknowledge?: boolean } = {}) {
  await userEvent.click(screen.getByText("Agregar condición"));
  set("Etiqueta de la condición", overrides.label ?? "Pregunta directa");
  set("Descripción de la condición", overrides.description ?? "La apertura se formula como una pregunta.");
  if (overrides.acknowledge !== false) await userEvent.click(screen.getByLabelText(ACKNOWLEDGEMENT_COPY));
}

beforeEach(() => {
  vi.clearAllMocks();
  uuidCounter = 0;
  vi.spyOn(crypto, "randomUUID").mockImplementation(
    () => `uuid-${++uuidCounter}` as ReturnType<typeof crypto.randomUUID>,
  );
  mockList.mockResolvedValue(page([]));
});

describe("ExperimentVariantsSection — zero declared conditions", () => {
  it("shows the empty state, does not fetch, and offers Agregar condición to a member", () => {
    renderSection(definition());
    expect(screen.getByText("Condiciones declaradas")).toBeInTheDocument();
    expect(screen.getByText(/Aún no se han declarado condiciones/)).toBeInTheDocument();
    expect(screen.getByText("Agregar condición")).toBeInTheDocument();
    expect(mockList).not.toHaveBeenCalled();
    expect(screen.queryByText(/fijada/)).not.toBeInTheDocument();
  });

  it("never offers the add control to a viewer without a role", () => {
    renderSection(definition(), { role: null });
    expect(screen.queryByText("Agregar condición")).not.toBeInTheDocument();
  });
});

describe("ExperimentVariantsSection — list", () => {
  it("fetches the first page (limit 100) when the count is above zero and renders the conditions in order", async () => {
    mockList.mockResolvedValue(page([variant(1), variant(2)]));
    renderSection(definition({ variant_count: 2, is_pinned: true }));
    await waitFor(() => expect(screen.getByText("Condición 1")).toBeInTheDocument());
    expect(mockList).toHaveBeenCalledTimes(1);
    expect(mockList).toHaveBeenCalledWith("campaign-1", "EXP-1", { limit: 100, offset: 0 });
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Condición 1");
    expect(items[1]).toHaveTextContent("Condición 2");
    expect(screen.getByText("Descripción 2")).toBeInTheDocument();
    expect(screen.queryByText("Ver más")).not.toBeInTheDocument();
    expect(screen.getByText(/está fijada por las condiciones declaradas/)).toBeInTheDocument();
  });

  it("offers Ver más when there are more conditions and appends the next page by offset", async () => {
    mockList
      .mockResolvedValueOnce(page([variant(1), variant(2)], 3))
      .mockResolvedValueOnce(page([variant(3)], 3, 2));
    renderSection(definition({ variant_count: 3, is_pinned: true }));
    await waitFor(() => expect(screen.getByText("Ver más")).toBeInTheDocument());
    expect(screen.getByText(/Mostrando 2 de 3/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("Ver más"));
    await waitFor(() => expect(screen.getByText("Condición 3")).toBeInTheDocument());
    expect(mockList).toHaveBeenLastCalledWith("campaign-1", "EXP-1", { limit: 100, offset: 2 });
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.queryByText("Ver más")).not.toBeInTheDocument();
  });

  it("shows a list-load error without leaking codes", async () => {
    mockList.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
    renderSection(definition({ variant_count: 1, is_pinned: true }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).not.toHaveTextContent("INTERNAL");
  });

  it("refetches when the derived count changes", async () => {
    mockList.mockResolvedValue(page([variant(1)]));
    const { rerender } = render(
      <ExperimentVariantsSection
        campaignId="campaign-1"
        experiment={EXPERIMENT}
        definition={definition({ variant_count: 1, is_pinned: true })}
        role="OWNER"
        onChanged={vi.fn()}
      />,
    );
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(1));
    rerender(
      <ExperimentVariantsSection
        campaignId="campaign-1"
        experiment={EXPERIMENT}
        definition={definition({ variant_count: 2, is_pinned: true })}
        role="OWNER"
        onChanged={vi.fn()}
      />,
    );
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
  });
});

describe("ExperimentVariantsSection — declaration form", () => {
  it("requires the acknowledgement before the submit control is available", async () => {
    renderSection(definition());
    await userEvent.click(screen.getByText("Agregar condición"));
    set("Etiqueta de la condición", "Pregunta directa");
    set("Descripción de la condición", "La apertura se formula como una pregunta.");
    expect(screen.getByText("Confirmar condición")).toBeDisabled();
    await userEvent.click(screen.getByLabelText(ACKNOWLEDGEMENT_COPY));
    expect(screen.getByText("Confirmar condición")).toBeEnabled();
    await userEvent.click(screen.getByLabelText(ACKNOWLEDGEMENT_COPY));
    expect(screen.getByText("Confirmar condición")).toBeDisabled();
    expect(mockDeclare).not.toHaveBeenCalled();
  });

  it("warns that the definition version is fixed and that a declared condition cannot currently be corrected — without claiming permanence", async () => {
    renderSection(definition());
    await userEvent.click(screen.getByText("Agregar condición"));
    expect(screen.getByText(/fija la versión actual de la definición de la comparación/)).toBeInTheDocument();
    expect(screen.getByText(/no ofrece una forma de corregir una condición ya declarada/)).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    for (const forbidden of [/para siempre/i, /permanente/i, /irreversible/i, /nuevo experimento/i, /nunca/i]) {
      expect(text).not.toMatch(forbidden);
    }
  });

  it("blocks blank and over-long fields without calling the API", async () => {
    renderSection(definition());
    await openAndFill({ label: "   " });
    await userEvent.click(screen.getByText("Confirmar condición"));
    expect(screen.getByRole("alert")).toHaveTextContent('El campo "Etiqueta de la condición" es obligatorio.');
    set("Etiqueta de la condición", "x".repeat(201));
    await userEvent.click(screen.getByText("Confirmar condición"));
    expect(screen.getByRole("alert")).toHaveTextContent("no puede superar 200 caracteres");
    set("Etiqueta de la condición", "Ok");
    set("Descripción de la condición", "");
    await userEvent.click(screen.getByText("Confirmar condición"));
    expect(screen.getByRole("alert")).toHaveTextContent('El campo "Descripción de la condición" es obligatorio.');
    set("Descripción de la condición", "d".repeat(1001));
    await userEvent.click(screen.getByText("Confirmar condición"));
    expect(screen.getByRole("alert")).toHaveTextContent("no puede superar 1000 caracteres");
    expect(mockDeclare).not.toHaveBeenCalled();
  });

  it("sends the explicit pin and trimmed fields, then closes the form, refetches the list, and refreshes the parent", async () => {
    mockDeclare.mockResolvedValue(variant(1));
    mockList.mockResolvedValue(page([variant(1)]));
    const { onChanged } = renderSection(definition({ variant_count: 1, is_pinned: true }));
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(1));
    await openAndFill({ label: "  Pregunta directa  ", description: "  La apertura es una pregunta.  " });
    await userEvent.click(screen.getByText("Confirmar condición"));

    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(1));
    expect(mockDeclare).toHaveBeenCalledWith("campaign-1", "EXP-1", {
      definition_version_id: "EXD-1",
      label: "Pregunta directa",
      condition_description: "La apertura es una pregunta.",
      client_request_id: "uuid-1",
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(screen.queryByLabelText("Etiqueta de la condición")).not.toBeInTheDocument();
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
  });

  it("cancel discards the draft without submitting", async () => {
    renderSection(definition());
    await openAndFill();
    await userEvent.click(screen.getByText("Cancelar"));
    expect(screen.queryByLabelText("Etiqueta de la condición")).not.toBeInTheDocument();
    expect(mockDeclare).not.toHaveBeenCalled();
    await userEvent.click(screen.getByText("Agregar condición"));
    expect((screen.getByLabelText("Etiqueta de la condición") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText(ACKNOWLEDGEMENT_COPY) as HTMLInputElement).checked).toBe(false);
  });

  it("blocks a double submit while a write is pending", async () => {
    let resolveWrite: (value: VariantPublic) => void = () => undefined;
    mockDeclare.mockReturnValue(new Promise((resolve) => (resolveWrite = resolve)));
    renderSection(definition());
    await openAndFill();
    const confirm = screen.getByText("Confirmar condición");
    await userEvent.click(confirm);
    await userEvent.click(confirm);
    expect(mockDeclare).toHaveBeenCalledTimes(1);
    resolveWrite(variant(1));
    await waitFor(() => expect(screen.queryByLabelText("Etiqueta de la condición")).not.toBeInTheDocument());
  });
});

describe("ExperimentVariantsSection — idempotency key", () => {
  it("keeps the same client_request_id across a retryable failure and rotates it after success", async () => {
    mockDeclare
      .mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR", "offline"))
      .mockResolvedValueOnce(variant(1))
      .mockResolvedValueOnce(variant(2));
    renderSection(definition());
    await openAndFill();
    await userEvent.click(screen.getByText("Confirmar condición"));
    await waitFor(() =>
      expect(screen.getByText("No pudimos conectar con el servidor. Verifica tu conexión.")).toBeInTheDocument(),
    );
    // The form and the draft survive a failure.
    expect((screen.getByLabelText("Etiqueta de la condición") as HTMLInputElement).value).toBe("Pregunta directa");
    await userEvent.click(screen.getByText("Confirmar condición"));
    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(2));
    expect(mockDeclare.mock.calls[0][2].client_request_id).toBe("uuid-1");
    expect(mockDeclare.mock.calls[1][2].client_request_id).toBe("uuid-1");

    await openAndFill({ label: "Otra", description: "Otra descripción." });
    await userEvent.click(screen.getByText("Confirmar condición"));
    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(3));
    expect(mockDeclare.mock.calls[2][2].client_request_id).toBe("uuid-2");
  });

  it("rotates the key on IDEMPOTENCY_KEY_CONFLICT and keeps the form open", async () => {
    mockDeclare
      .mockRejectedValueOnce(new ApiError(409, "IDEMPOTENCY_KEY_CONFLICT", "conflict"))
      .mockResolvedValueOnce(variant(1));
    renderSection(definition());
    await openAndFill();
    await userEvent.click(screen.getByText("Confirmar condición"));
    await waitFor(() =>
      expect(
        screen.getByText("Esta solicitud no coincide con un envío anterior. Revisa los datos e inténtalo de nuevo."),
      ).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Etiqueta de la condición")).toBeInTheDocument();
    expect(screen.queryByText(/IDEMPOTENCY_KEY_CONFLICT/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("Confirmar condición"));
    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(2));
    expect(mockDeclare.mock.calls[0][2].client_request_id).toBe("uuid-1");
    expect(mockDeclare.mock.calls[1][2].client_request_id).toBe("uuid-2");
  });
});

describe("ExperimentVariantsSection — conflicts", () => {
  it("on LABEL_DUPLICATE shows the conflict and preserves the editable form", async () => {
    mockDeclare.mockRejectedValue(new ApiError(409, "EXPERIMENT_VARIANT_LABEL_DUPLICATE", "dup"));
    const { onChanged } = renderSection(definition());
    await openAndFill();
    await userEvent.click(screen.getByText("Confirmar condición"));
    await waitFor(() =>
      expect(screen.getByText("Ya existe una condición con esa etiqueta para esta versión de la definición.")).toBeInTheDocument(),
    );
    expect((screen.getByLabelText("Etiqueta de la condición") as HTMLInputElement).value).toBe("Pregunta directa");
    expect((screen.getByLabelText(ACKNOWLEDGEMENT_COPY) as HTMLInputElement).checked).toBe(true);
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("on VERSION_NOT_CURRENT discards the draft, refetches, and explains — never silently rebasing", async () => {
    mockDeclare.mockRejectedValue(new ApiError(409, "EXPERIMENT_VARIANT_DEFINITION_VERSION_NOT_CURRENT", "stale"));
    const { onChanged } = renderSection(definition());
    await openAndFill();
    await userEvent.click(screen.getByText("Confirmar condición"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("alert")).toHaveTextContent("La definición cambió mientras declarabas la condición");
    expect(screen.queryByLabelText("Etiqueta de la condición")).not.toBeInTheDocument();
    expect(mockDeclare).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByText("Agregar condición"));
    expect((screen.getByLabelText("Etiqueta de la condición") as HTMLInputElement).value).toBe("");
  });

  it("on STRATEGY_STALE refetches and explains that no new conditions are accepted", async () => {
    mockDeclare.mockRejectedValue(new ApiError(409, "EXPERIMENT_VARIANT_STRATEGY_STALE", "stale"));
    const { onChanged } = renderSection(definition());
    await openAndFill();
    await userEvent.click(screen.getByText("Confirmar condición"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("alert")).toHaveTextContent("ya no es la vigente");
  });

  it("does not leak raw error codes", async () => {
    mockDeclare.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
    renderSection(definition());
    await openAndFill();
    await userEvent.click(screen.getByText("Confirmar condición"));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).not.toHaveTextContent("INTERNAL");
  });
});

describe("ExperimentVariantsSection — scope firewall", () => {
  it("exposes no role, allocation, traffic, metric, winner, result, correction or execution control", async () => {
    mockList.mockResolvedValue(page([variant(1)]));
    renderSection(definition({ variant_count: 1, is_pinned: true }));
    await waitFor(() => expect(screen.getByText("Condición 1")).toBeInTheDocument());
    await userEvent.click(screen.getByText("Agregar condición"));
    const forbidden = [
      /rol\b/i, /control(?!ad)/i, /tratamiento/i, /asignaci/i, /tráfico/i, /peso/i, /métrica/i, /ganador/i,
      /resultado/i, /ejecut/i, /editar/i, /corregir/i, /eliminar/i, /retirar/i, /variante/i,
    ];
    for (const control of [...screen.queryAllByRole("button"), ...screen.queryAllByRole("combobox"), ...screen.queryAllByRole("spinbutton")]) {
      const name = control.textContent ?? "";
      for (const pattern of forbidden) expect(name).not.toMatch(pattern);
    }
    expect(screen.queryAllByRole("combobox")).toHaveLength(0);
  });
});
