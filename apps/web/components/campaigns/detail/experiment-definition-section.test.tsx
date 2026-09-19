import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExperimentDefinitionSection } from "@/components/campaigns/detail/experiment-definition-section";
import { ApiError } from "@/lib/api/client";
import type { ExperimentDefinitionPublic, ExperimentPublic } from "@/types/strategy";

vi.mock("@/lib/api/strategy", () => ({
  declareExperimentDefinition: vi.fn(),
  declareVariant: vi.fn(),
  listVariants: vi.fn(),
}));

import { declareExperimentDefinition, listVariants } from "@/lib/api/strategy";

const mockDeclare = vi.mocked(declareExperimentDefinition);

let uuidCounter = 0;

function definition(overrides: Partial<ExperimentDefinitionPublic> = {}): ExperimentDefinitionPublic {
  return {
    id: "EXD-1",
    experiment_id: "EXP-1",
    version: 1,
    comparison_question: "Does a question hook change completion?",
    comparison_type: "OBSERVATIONAL",
    changed_factor: "Opening hook",
    controlled_factors: [],
    comparison_basis: "The current hook.",
    scope: "Reels, one month.",
    learning_intent: "Choose the next hook style.",
    non_conclusion_boundary: "Does not establish causality.",
    non_conclusion_codes: [
      "NO_ATTRIBUTION_ESTABLISHED",
      "NO_STATISTICAL_VALIDITY_ESTABLISHED",
      "NO_RESULT_OR_WINNER",
      "CANNOT_ESTABLISH_CAUSALITY",
    ],
    created_at: "2026-01-01T00:00:00Z",
    variant_count: 0,
    is_pinned: false,
    ...overrides,
  };
}

function experiment(overrides: Partial<ExperimentPublic> = {}): ExperimentPublic {
  return {
    id: "EXP-1",
    hypothesis_id: "HYP-1",
    description: "Compare two hooks.",
    status: "RECORDED",
    created_at: "2026-01-01T00:00:00Z",
    comparison_label: "NO_COMPARISON_DECLARED",
    definition: null,
    ...overrides,
  };
}

function defined(overrides: Partial<ExperimentDefinitionPublic> = {}): ExperimentPublic {
  const tip = definition(overrides);
  return experiment({
    comparison_label:
      tip.comparison_type === "CONTROLLED" ? "DECLARED_CONTROLLED_INTENT" : "DECLARED_OBSERVATIONAL_INTENT",
    definition: tip,
  });
}

function renderSection(
  subject: ExperimentPublic,
  { role = "OWNER", onChanged = vi.fn() }: { role?: string | null; onChanged?: () => void } = {},
) {
  render(<ExperimentDefinitionSection campaignId="campaign-1" experiment={subject} role={role} onChanged={onChanged} />);
  return { onChanged };
}

function set(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function fillValid(overrides: Record<string, string> = {}) {
  const values: Record<string, string> = {
    "Pregunta de comparación": "Does a stronger CTA change click-through?",
    "Factor que cambia": "Call to action",
    "Factores controlados (uno por línea)": "",
    "Base de comparación": "The current CTA.",
    Alcance: "Reels, existing followers.",
    "Intención de aprendizaje": "Pick the CTA for the next cycle.",
    "Límite de conclusión (lo que esta comparación NO establece)": "Does not establish causality.",
    ...overrides,
  };
  for (const [label, value] of Object.entries(values)) set(label, value);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listVariants).mockResolvedValue({ experiment_id: "EXP-1", items: [], limit: 100, offset: 0, total: 0 });
  uuidCounter = 0;
  vi.spyOn(crypto, "randomUUID").mockImplementation(
    () => `uuid-${++uuidCounter}` as ReturnType<typeof crypto.randomUUID>,
  );
});

describe("ExperimentDefinitionSection — no definition", () => {
  it("shows the no-comparison state and a declare control for a member", () => {
    renderSection(experiment());
    expect(screen.getByText("Sin comparación declarada")).toBeInTheDocument();
    expect(screen.getByText("Declarar comparación")).toBeInTheDocument();
    expect(screen.queryByText(/versión/)).not.toBeInTheDocument();
  });

  it("never offers a declare control to an unauthenticated viewer", () => {
    renderSection(experiment(), { role: null });
    expect(screen.getByText("Sin comparación declarada")).toBeInTheDocument();
    expect(screen.queryByText("Declarar comparación")).not.toBeInTheDocument();
  });

  it("opens an empty form defaulting to an observational comparison", async () => {
    renderSection(experiment());
    await userEvent.click(screen.getByText("Declarar comparación"));
    expect((screen.getByLabelText("Tipo de comparación") as HTMLSelectElement).value).toBe("OBSERVATIONAL");
    expect((screen.getByLabelText("Pregunta de comparación") as HTMLTextAreaElement).value).toBe("");
    expect(screen.getByText("Confirmar comparación")).toBeInTheDocument();
    expect(screen.getByText(/no equivale a\s+una audiencia gobernada/)).toBeInTheDocument();
  });
});

describe("ExperimentDefinitionSection — validation", () => {
  async function openAndSubmit(fill: () => void) {
    renderSection(experiment());
    await userEvent.click(screen.getByText("Declarar comparación"));
    fill();
    await userEvent.click(screen.getByText("Confirmar comparación"));
  }

  it("blocks a blank required field without calling the API", async () => {
    await openAndSubmit(() => fillValid({ "Pregunta de comparación": "   " }));
    expect(screen.getByRole("alert")).toHaveTextContent('El campo "Pregunta de comparación" es obligatorio.');
    expect(mockDeclare).not.toHaveBeenCalled();
  });

  it("blocks a blank changed factor", async () => {
    await openAndSubmit(() => fillValid({ "Factor que cambia": "" }));
    expect(screen.getByRole("alert")).toHaveTextContent('El campo "Factor que cambia" es obligatorio.');
    expect(mockDeclare).not.toHaveBeenCalled();
  });

  it("blocks over-long prose", async () => {
    await openAndSubmit(() => fillValid({ Alcance: "x".repeat(1001) }));
    expect(screen.getByRole("alert")).toHaveTextContent("no puede superar 1000 caracteres");
    expect(mockDeclare).not.toHaveBeenCalled();
  });

  it("blocks a controlled comparison with no controlled factor", async () => {
    await openAndSubmit(() => {
      fireEvent.change(screen.getByLabelText("Tipo de comparación"), { target: { value: "CONTROLLED" } });
      fillValid();
    });
    expect(screen.getByRole("alert")).toHaveTextContent("requiere al menos un factor controlado");
    expect(mockDeclare).not.toHaveBeenCalled();
  });

  it("blocks duplicate controlled factors (case- and spacing-insensitive)", async () => {
    await openAndSubmit(() => fillValid({ "Factores controlados (uno por línea)": "Post  time\npost time" }));
    expect(screen.getByRole("alert")).toHaveTextContent("no pueden repetirse");
    expect(mockDeclare).not.toHaveBeenCalled();
  });

  it("blocks a changed factor that is also a controlled factor", async () => {
    await openAndSubmit(() => fillValid({ "Factores controlados (uno por línea)": "CALL TO ACTION" }));
    expect(screen.getByRole("alert")).toHaveTextContent("no puede ser también un factor controlado");
    expect(mockDeclare).not.toHaveBeenCalled();
  });

  it("blocks more than 20 controlled factors", async () => {
    const factors = Array.from({ length: 21 }, (_, index) => `factor ${index}`).join("\n");
    await openAndSubmit(() => fillValid({ "Factores controlados (uno por línea)": factors }));
    expect(screen.getByRole("alert")).toHaveTextContent("No puede haber más de 20 factores controlados");
    expect(mockDeclare).not.toHaveBeenCalled();
  });
});

describe("ExperimentDefinitionSection — declare submission", () => {
  it("sends a full-state payload with base_version 0, trimmed fields, and refreshes on success", async () => {
    mockDeclare.mockResolvedValue(definition());
    const { onChanged } = renderSection(experiment());
    await userEvent.click(screen.getByText("Declarar comparación"));
    fireEvent.change(screen.getByLabelText("Tipo de comparación"), { target: { value: "CONTROLLED" } });
    fillValid({
      "Factor que cambia": "  Call to action  ",
      "Factores controlados (uno por línea)": "  Posting time  \n\nFormat\n",
    });
    await userEvent.click(screen.getByText("Confirmar comparación"));

    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(1));
    expect(mockDeclare).toHaveBeenCalledWith("campaign-1", "EXP-1", {
      base_version: 0,
      client_request_id: "uuid-1",
      comparison_question: "Does a stronger CTA change click-through?",
      comparison_type: "CONTROLLED",
      changed_factor: "Call to action",
      controlled_factors: ["Posting time", "Format"],
      comparison_basis: "The current CTA.",
      scope: "Reels, existing followers.",
      learning_intent: "Pick the CTA for the next cycle.",
      non_conclusion_boundary: "Does not establish causality.",
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(screen.queryByLabelText("Pregunta de comparación")).not.toBeInTheDocument();
  });

  it("cancel discards the draft without submitting", async () => {
    renderSection(experiment());
    await userEvent.click(screen.getByText("Declarar comparación"));
    fillValid();
    await userEvent.click(screen.getByText("Cancelar"));
    expect(screen.queryByLabelText("Pregunta de comparación")).not.toBeInTheDocument();
    expect(mockDeclare).not.toHaveBeenCalled();
    await userEvent.click(screen.getByText("Declarar comparación"));
    expect((screen.getByLabelText("Pregunta de comparación") as HTMLTextAreaElement).value).toBe("");
  });
});

describe("ExperimentDefinitionSection — tip rendering and safe labels", () => {
  it("renders an observational tip with its declaration-only label and restrictions", () => {
    renderSection(defined());
    expect(screen.getByText("Comparación observacional declarada")).toBeInTheDocument();
    expect(screen.getByText(/versión 1/)).toBeInTheDocument();
    expect(screen.getByText("Does a question hook change completion?")).toBeInTheDocument();
    expect(screen.getByText("Opening hook")).toBeInTheDocument();
    expect(screen.getByText("Ninguno declarado")).toBeInTheDocument();
    expect(screen.getByText("No establece atribución.")).toBeInTheDocument();
    expect(screen.getByText("No establece validez estadística.")).toBeInTheDocument();
    expect(screen.getByText("No declara un resultado ni un ganador.")).toBeInTheDocument();
    expect(screen.getByText("Una comparación observacional no puede establecer causalidad.")).toBeInTheDocument();
    expect(screen.getByText(/no constituye pre-registro/)).toBeInTheDocument();
    expect(screen.getByText("Revisar comparación")).toBeInTheDocument();
    expect(screen.queryByText("Declarar comparación")).not.toBeInTheDocument();
  });

  it("renders a controlled tip as design intent only, never as validated control", () => {
    renderSection(
      defined({
        version: 3,
        comparison_type: "CONTROLLED",
        controlled_factors: ["Posting time", "Format"],
        non_conclusion_codes: [
          "NO_ATTRIBUTION_ESTABLISHED",
          "NO_STATISTICAL_VALIDITY_ESTABLISHED",
          "NO_RESULT_OR_WINNER",
          "CAUSALITY_NOT_ESTABLISHED_BY_DEFINITION",
          "CONTROLLED_VALIDITY_NOT_ESTABLISHED",
        ],
      }),
    );
    expect(screen.getByText("Comparación controlada declarada (solo intención de diseño)")).toBeInTheDocument();
    expect(screen.getByText(/versión 3/)).toBeInTheDocument();
    expect(screen.getByText("Posting time, Format")).toBeInTheDocument();
    expect(screen.getByText("La sola declaración no establece causalidad.")).toBeInTheDocument();
    expect(
      screen.getByText("La validez del control no está establecida: es únicamente la intención de diseño declarada."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/experimento válido/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/causalidad establecida/i)).not.toBeInTheDocument();
  });

  it("does not offer the write control to a viewer without a role", () => {
    renderSection(defined(), { role: null });
    expect(screen.getByText("Comparación observacional declarada")).toBeInTheDocument();
    expect(screen.queryByText("Revisar comparación")).not.toBeInTheDocument();
  });

  it("exposes no history, Variant, measurement, or execution control", () => {
    renderSection(defined());
    for (const forbidden of [/historial/i, /variante/i, /medición/i, /ejecutar/i, /ganador confirmado/i]) {
      expect(screen.queryByRole("button", { name: forbidden })).not.toBeInTheDocument();
    }
  });
});

describe("ExperimentDefinitionSection — revision", () => {
  it("preloads the full current definition and sends base_version = the current tip", async () => {
    mockDeclare.mockResolvedValue(definition({ version: 3 }));
    const { onChanged } = renderSection(
      defined({
        version: 2,
        comparison_type: "CONTROLLED",
        changed_factor: "Call to action",
        controlled_factors: ["Posting time", "Format"],
      }),
    );
    await userEvent.click(screen.getByText("Revisar comparación"));

    expect((screen.getByLabelText("Tipo de comparación") as HTMLSelectElement).value).toBe("CONTROLLED");
    expect((screen.getByLabelText("Pregunta de comparación") as HTMLTextAreaElement).value).toBe(
      "Does a question hook change completion?",
    );
    expect((screen.getByLabelText("Factor que cambia") as HTMLInputElement).value).toBe("Call to action");
    expect((screen.getByLabelText("Factores controlados (uno por línea)") as HTMLTextAreaElement).value).toBe(
      "Posting time\nFormat",
    );
    expect((screen.getByLabelText("Base de comparación") as HTMLTextAreaElement).value).toBe("The current hook.");
    expect((screen.getByLabelText("Alcance") as HTMLTextAreaElement).value).toBe("Reels, one month.");
    expect((screen.getByLabelText("Intención de aprendizaje") as HTMLTextAreaElement).value).toBe(
      "Choose the next hook style.",
    );
    expect(
      (screen.getByLabelText("Límite de conclusión (lo que esta comparación NO establece)") as HTMLTextAreaElement).value,
    ).toBe("Does not establish causality.");

    set("Alcance", "Reels, two months.");
    await userEvent.click(screen.getByText("Confirmar revisión"));
    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(1));
    expect(mockDeclare.mock.calls[0][2]).toMatchObject({
      base_version: 2,
      scope: "Reels, two months.",
      controlled_factors: ["Posting time", "Format"],
      comparison_type: "CONTROLLED",
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  });
});

describe("ExperimentDefinitionSection — idempotency key", () => {
  it("keeps the same client_request_id across a retryable failure and rotates it after success", async () => {
    mockDeclare
      .mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR", "offline"))
      .mockResolvedValueOnce(definition())
      .mockResolvedValueOnce(definition({ version: 2 }));
    renderSection(experiment());

    await userEvent.click(screen.getByText("Declarar comparación"));
    fillValid();
    await userEvent.click(screen.getByText("Confirmar comparación"));
    await waitFor(() =>
      expect(screen.getByText("No pudimos conectar con el servidor. Verifica tu conexión.")).toBeInTheDocument(),
    );
    // The form and the draft survive a failure.
    expect((screen.getByLabelText("Alcance") as HTMLTextAreaElement).value).toBe("Reels, existing followers.");

    await userEvent.click(screen.getByText("Confirmar comparación"));
    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(2));
    expect(mockDeclare.mock.calls[0][2].client_request_id).toBe(mockDeclare.mock.calls[1][2].client_request_id);
    expect(mockDeclare.mock.calls[0][2].client_request_id).toBe("uuid-1");

    // After the success a NEW write (the section is still rendered without a
    // refreshed prop) uses a fresh key.
    await userEvent.click(screen.getByText("Declarar comparación"));
    fillValid();
    await userEvent.click(screen.getByText("Confirmar comparación"));
    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(3));
    expect(mockDeclare.mock.calls[2][2].client_request_id).toBe("uuid-2");
  });

  it("rotates the key on IDEMPOTENCY_KEY_CONFLICT and keeps the form open", async () => {
    mockDeclare
      .mockRejectedValueOnce(new ApiError(409, "IDEMPOTENCY_KEY_CONFLICT", "conflict"))
      .mockResolvedValueOnce(definition());
    renderSection(experiment());
    await userEvent.click(screen.getByText("Declarar comparación"));
    fillValid();
    await userEvent.click(screen.getByText("Confirmar comparación"));
    await waitFor(() =>
      expect(screen.getByText("Esta solicitud no coincide con un envío anterior. Revisa los datos e inténtalo de nuevo.")).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Pregunta de comparación")).toBeInTheDocument();
    expect(screen.queryByText(/IDEMPOTENCY_KEY_CONFLICT/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByText("Confirmar comparación"));
    await waitFor(() => expect(mockDeclare).toHaveBeenCalledTimes(2));
    expect(mockDeclare.mock.calls[0][2].client_request_id).toBe("uuid-1");
    expect(mockDeclare.mock.calls[1][2].client_request_id).toBe("uuid-2");
  });
});

describe("ExperimentDefinitionSection — stale and conflict handling", () => {
  it("on BASE_STALE discards the draft, refetches, and explains — never silently rebasing", async () => {
    mockDeclare.mockRejectedValue(new ApiError(409, "EXPERIMENT_DEFINITION_BASE_STALE", "stale"));
    const { onChanged } = renderSection(defined({ version: 1 }));
    await userEvent.click(screen.getByText("Revisar comparación"));
    set("Alcance", "A draft that must not be silently rebased.");
    await userEvent.click(screen.getByText("Confirmar revisión"));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("alert")).toHaveTextContent("La definición cambió mientras la editabas");
    expect(screen.queryByLabelText("Alcance")).not.toBeInTheDocument();
    expect(mockDeclare).toHaveBeenCalledTimes(1);
    // Re-opening starts from the (refetched) tip, not from the discarded draft.
    await userEvent.click(screen.getByText("Revisar comparación"));
    expect((screen.getByLabelText("Alcance") as HTMLTextAreaElement).value).toBe("Reels, one month.");
  });

  it("on STRATEGY_STALE refetches and explains that the definition cannot be modified", async () => {
    mockDeclare.mockRejectedValue(new ApiError(409, "EXPERIMENT_DEFINITION_STRATEGY_STALE", "stale"));
    const { onChanged } = renderSection(experiment());
    await userEvent.click(screen.getByText("Declarar comparación"));
    fillValid();
    await userEvent.click(screen.getByText("Confirmar comparación"));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("alert")).toHaveTextContent("ya no es la vigente");
  });

  it("on UNCHANGED shows the message and keeps the form open without refetching", async () => {
    mockDeclare.mockRejectedValue(new ApiError(409, "EXPERIMENT_DEFINITION_UNCHANGED", "unchanged"));
    const { onChanged } = renderSection(defined());
    await userEvent.click(screen.getByText("Revisar comparación"));
    await userEvent.click(screen.getByText("Confirmar revisión"));
    await waitFor(() => expect(screen.getByText("No hay cambios respecto a la definición vigente.")).toBeInTheDocument());
    expect(screen.getByLabelText("Alcance")).toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("does not leak raw error codes", async () => {
    mockDeclare.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
    renderSection(experiment());
    await userEvent.click(screen.getByText("Declarar comparación"));
    fillValid();
    await userEvent.click(screen.getByText("Confirmar comparación"));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).not.toHaveTextContent("INTERNAL");
  });

  it("blocks a double submit while a write is pending", async () => {
    let resolveWrite: (value: ExperimentDefinitionPublic) => void = () => undefined;
    mockDeclare.mockReturnValue(new Promise((resolve) => (resolveWrite = resolve)));
    renderSection(experiment());
    await userEvent.click(screen.getByText("Declarar comparación"));
    fillValid();
    const confirm = screen.getByText("Confirmar comparación");
    await userEvent.click(confirm);
    await userEvent.click(confirm);
    expect(mockDeclare).toHaveBeenCalledTimes(1);
    resolveWrite(definition());
    await waitFor(() => expect(screen.queryByLabelText("Alcance")).not.toBeInTheDocument());
  });
});

describe("ExperimentDefinitionSection — pinned definition (MVP-38)", () => {
  it("disables the revision control with an explanation when the definition is pinned", () => {
    renderSection(defined({ variant_count: 2, is_pinned: true }));
    expect(screen.getByText("Revisar comparación")).toBeDisabled();
    expect(screen.getByText(/está fijada por condiciones declaradas y no admite una nueva versión/)).toBeInTheDocument();
    expect(screen.getByText("Condiciones declaradas")).toBeInTheDocument();
  });

  it("keeps the revision control enabled while no condition is declared, and renders the conditions child", () => {
    renderSection(defined());
    expect(screen.getByText("Revisar comparación")).toBeEnabled();
    expect(screen.getByText("Condiciones declaradas")).toBeInTheDocument();
    expect(screen.getByText("Agregar condición")).toBeInTheDocument();
  });

  it("does not render the conditions child without a definition", () => {
    renderSection(experiment());
    expect(screen.queryByText("Condiciones declaradas")).not.toBeInTheDocument();
  });

  it("on a backend PINNED conflict closes the revision form, refetches, and explains — the backend stays authoritative", async () => {
    mockDeclare.mockRejectedValue(new ApiError(409, "EXPERIMENT_DEFINITION_PINNED", "pinned"));
    const { onChanged } = renderSection(defined());
    await userEvent.click(screen.getByText("Revisar comparación"));
    set("Alcance", "A draft that races a declared condition.");
    await userEvent.click(screen.getByText("Confirmar revisión"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(screen.getByText(/está fijada por condiciones declaradas, por lo que no admite una nueva versión/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Alcance")).not.toBeInTheDocument();
  });
});
