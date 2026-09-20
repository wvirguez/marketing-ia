"use client";

// MVP-37: governed Experiment Definition — the declared comparison design of
// ONE Experiment (frozen MVP-37A/-37B). Minimum reachable UI only: declare
// (no definition yet), read the current tip with its safe declaration-only
// label, and revise (a preloaded full-state form that appends a new
// immutable version). There is no history UI, no Variant UI, no measurement
// UI and no execution UI — none of those exist in MVP-37. (MVP-38 renders the
// declared-conditions child section below once a definition exists.)
//
// DECLARED != PRE-REGISTERED. DECLARED CONTROLLED INTENT != CONTROLLED
// EXPERIMENT. Nothing here claims validity, causality, a result, a winner,
// or execution authorization, and the copy below never does.
//
// Idempotency: `client_request_id` is generated once per section and KEPT
// across retryable failures (so a lost response replays as a 200), and
// rotated only on a successful write or IDEMPOTENCY_KEY_CONFLICT.
// `base_version` is read from the current tip at submit time (0 when no
// definition exists). On BASE_STALE the draft is discarded and the view is
// refetched — a stale draft is never silently rebased over a newer tip.

import { useRef, useState } from "react";
import { declareExperimentDefinition } from "@/lib/api/strategy";
import { ApiError } from "@/lib/api/client";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { ComparisonType, ExperimentPublic } from "@/types/strategy";
import { ExperimentVariantsSection } from "./experiment-variants-section";
import { ExecutionAuthorizationSection } from "./execution-authorization-section";
import { MeasurementContractSection } from "./measurement-contract-section";

const PROSE_MAX = 1000;
const FACTOR_MAX = 200;
const MAX_CONTROLLED_FACTORS = 20;

export const NO_DEFINITION_COPY = "Sin comparación declarada";

const LABEL_COPY: Record<string, string> = {
  NO_COMPARISON_DECLARED: NO_DEFINITION_COPY,
  DECLARED_OBSERVATIONAL_INTENT: "Comparación observacional declarada",
  DECLARED_CONTROLLED_INTENT: "Comparación controlada declarada (solo intención de diseño)",
};

const NON_CONCLUSION_COPY: Record<string, string> = {
  NO_ATTRIBUTION_ESTABLISHED: "No establece atribución.",
  NO_STATISTICAL_VALIDITY_ESTABLISHED: "No establece validez estadística.",
  NO_RESULT_OR_WINNER: "No declara un resultado ni un ganador.",
  CANNOT_ESTABLISH_CAUSALITY: "Una comparación observacional no puede establecer causalidad.",
  CAUSALITY_NOT_ESTABLISHED_BY_DEFINITION: "La sola declaración no establece causalidad.",
  CONTROLLED_VALIDITY_NOT_ESTABLISHED:
    "La validez del control no está establecida: es únicamente la intención de diseño declarada.",
};

const TYPE_COPY: Record<ComparisonType, string> = {
  OBSERVATIONAL: "Observacional",
  CONTROLLED: "Controlada (intención de diseño)",
};

// MVP-32A §R precedent: the same MEMBER+ tier that may propose an Experiment.
function canDeclareDefinition(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN" || role === "MEMBER";
}

interface FormValues {
  comparisonType: ComparisonType;
  comparisonQuestion: string;
  changedFactor: string;
  controlledFactors: string; // one factor per line
  comparisonBasis: string;
  scope: string;
  learningIntent: string;
  nonConclusionBoundary: string;
}

const EMPTY_FORM: FormValues = {
  comparisonType: "OBSERVATIONAL",
  comparisonQuestion: "",
  changedFactor: "",
  controlledFactors: "",
  comparisonBasis: "",
  scope: "",
  learningIntent: "",
  nonConclusionBoundary: "",
};

// Comparison key used ONLY to detect duplicate / overlapping factors
// (mirrors the backend); never used to rewrite what is sent.
function factorKey(value: string): string {
  return value.split(/\s+/).filter(Boolean).join(" ").toLowerCase();
}

function parseFactors(raw: string): string[] {
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function validateDefinitionForm(values: FormValues): string | null {
  const prose: Array<[string, string]> = [
    ["Pregunta de comparación", values.comparisonQuestion],
    ["Base de comparación", values.comparisonBasis],
    ["Alcance", values.scope],
    ["Intención de aprendizaje", values.learningIntent],
    ["Límite de conclusión", values.nonConclusionBoundary],
  ];
  for (const [name, value] of prose) {
    const trimmed = value.trim();
    if (trimmed.length === 0) return `El campo "${name}" es obligatorio.`;
    if (trimmed.length > PROSE_MAX) return `El campo "${name}" no puede superar ${PROSE_MAX} caracteres.`;
  }
  const changed = values.changedFactor.trim();
  if (changed.length === 0) return 'El campo "Factor que cambia" es obligatorio.';
  if (changed.length > FACTOR_MAX) {
    return `El campo "Factor que cambia" no puede superar ${FACTOR_MAX} caracteres.`;
  }

  const factors = parseFactors(values.controlledFactors);
  if (values.comparisonType === "CONTROLLED" && factors.length === 0) {
    return "Una comparación controlada requiere al menos un factor controlado.";
  }
  if (factors.length > MAX_CONTROLLED_FACTORS) {
    return `No puede haber más de ${MAX_CONTROLLED_FACTORS} factores controlados.`;
  }
  const seen = new Set<string>();
  for (const factor of factors) {
    if (factor.length > FACTOR_MAX) return `Cada factor controlado no puede superar ${FACTOR_MAX} caracteres.`;
    const key = factorKey(factor);
    if (seen.has(key)) return "Los factores controlados no pueden repetirse.";
    seen.add(key);
  }
  if (seen.has(factorKey(changed))) return "El factor que cambia no puede ser también un factor controlado.";
  return null;
}

function valuesFromDefinition(experiment: ExperimentPublic): FormValues {
  const definition = experiment.definition;
  if (!definition) return { ...EMPTY_FORM };
  return {
    comparisonType: definition.comparison_type,
    comparisonQuestion: definition.comparison_question,
    changedFactor: definition.changed_factor,
    controlledFactors: definition.controlled_factors.join("\n"),
    comparisonBasis: definition.comparison_basis,
    scope: definition.scope,
    learningIntent: definition.learning_intent,
    nonConclusionBoundary: definition.non_conclusion_boundary,
  };
}

function describeDefinitionError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "EXPERIMENT_DEFINITION_BASE_STALE":
        return "La definición cambió mientras la editabas. Se actualizó la vista con la versión vigente; revísala y vuelve a intentar.";
      case "EXPERIMENT_DEFINITION_STRATEGY_STALE":
        return "Este experimento pertenece a una versión de la estrategia que ya no es la vigente, por lo que su definición no puede modificarse.";
      case "EXPERIMENT_DEFINITION_PINNED":
        return "Esta versión de la definición está fijada por condiciones declaradas, por lo que no admite una nueva versión.";
      case "EXPERIMENT_DEFINITION_UNCHANGED":
        return "No hay cambios respecto a la definición vigente.";
      case "IDEMPOTENCY_KEY_CONFLICT":
        return "Esta solicitud no coincide con un envío anterior. Revisa los datos e inténtalo de nuevo.";
    }
  }
  return describeCampaignError(error);
}

export function ExperimentDefinitionSection({
  campaignId,
  experiment,
  role,
  onChanged,
}: {
  campaignId: string;
  experiment: ExperimentPublic;
  role: string | null;
  onChanged: () => void;
}) {
  const definition = experiment.definition;
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<FormValues>({ ...EMPTY_FORM });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  // Lazy state initializer: the initial key is generated exactly once.
  const [initialClientRequestId] = useState(() => crypto.randomUUID());
  const clientRequestIdRef = useRef(initialClientRequestId);
  const idPrefix = `definition-${experiment.id}`;

  function update<K extends keyof FormValues>(key: K, value: FormValues[K]) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function openForm() {
    setValues(valuesFromDefinition(experiment));
    setError("");
    setOpen(true);
  }

  function closeForm() {
    setOpen(false);
    setValues({ ...EMPTY_FORM });
  }

  async function submit() {
    if (pending) return;
    const invalid = validateDefinitionForm(values);
    if (invalid) {
      setError(invalid);
      return;
    }
    setPending(true);
    setError("");
    try {
      await declareExperimentDefinition(campaignId, experiment.id, {
        base_version: definition?.version ?? 0,
        client_request_id: clientRequestIdRef.current,
        comparison_question: values.comparisonQuestion.trim(),
        comparison_type: values.comparisonType,
        changed_factor: values.changedFactor.trim(),
        controlled_factors: parseFactors(values.controlledFactors),
        comparison_basis: values.comparisonBasis.trim(),
        scope: values.scope.trim(),
        learning_intent: values.learningIntent.trim(),
        non_conclusion_boundary: values.nonConclusionBoundary.trim(),
      });
      clientRequestIdRef.current = crypto.randomUUID();
      closeForm();
      onChanged();
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : null;
      if (code === "IDEMPOTENCY_KEY_CONFLICT") {
        clientRequestIdRef.current = crypto.randomUUID();
      }
      setError(describeDefinitionError(caught));
      if (
        code === "EXPERIMENT_DEFINITION_BASE_STALE" ||
        code === "EXPERIMENT_DEFINITION_STRATEGY_STALE" ||
        code === "EXPERIMENT_DEFINITION_PINNED"
      ) {
        // Never silently rebase a stale draft over a newer tip: discard it,
        // close the form and refetch the current state.
        closeForm();
        onChanged();
      }
    } finally {
      setPending(false);
    }
  }

  const label = LABEL_COPY[experiment.comparison_label] ?? experiment.comparison_label;

  return (
    <div style={{ marginTop: 8 }}>
      <p className="muted small-text">
        <strong>{label}</strong>
        {definition ? ` · versión ${definition.version}` : ""}
      </p>

      {definition && (
        <dl className="small-text" style={{ marginTop: 4 }}>
          <dt>Tipo de comparación (intención declarada)</dt>
          <dd>{TYPE_COPY[definition.comparison_type] ?? definition.comparison_type}</dd>
          <dt>Pregunta de comparación</dt>
          <dd style={{ whiteSpace: "pre-wrap" }}>{definition.comparison_question}</dd>
          <dt>Factor que cambia</dt>
          <dd>{definition.changed_factor}</dd>
          <dt>Factores controlados</dt>
          <dd>{definition.controlled_factors.length > 0 ? definition.controlled_factors.join(", ") : "Ninguno declarado"}</dd>
          <dt>Base de comparación</dt>
          <dd style={{ whiteSpace: "pre-wrap" }}>{definition.comparison_basis}</dd>
          <dt>Alcance (no es una audiencia gobernada)</dt>
          <dd style={{ whiteSpace: "pre-wrap" }}>{definition.scope}</dd>
          <dt>Intención de aprendizaje</dt>
          <dd style={{ whiteSpace: "pre-wrap" }}>{definition.learning_intent}</dd>
          <dt>Límite de conclusión</dt>
          <dd style={{ whiteSpace: "pre-wrap" }}>{definition.non_conclusion_boundary}</dd>
        </dl>
      )}

      {definition && (
        <ul className="muted small-text" style={{ marginTop: 4 }}>
          {definition.non_conclusion_codes.map((code) => (
            <li key={code}>{NON_CONCLUSION_COPY[code] ?? code}</li>
          ))}
        </ul>
      )}

      <p className="muted small-text" style={{ marginTop: 4 }}>
        Declarar una comparación describe el diseño previsto. No es un resultado, no constituye pre-registro, no
        define mediciones y no autoriza ejecución.
      </p>

      {error && !open && (
        <p role="alert" className="settings-feedback">
          {error}
        </p>
      )}

      {canDeclareDefinition(role) && !open && (
        <div className="settings-form-actions" style={{ marginTop: 8 }}>
          {/* The backend remains authoritative (EXPERIMENT_DEFINITION_PINNED);
              disabling here only avoids a doomed request. */}
          <button type="button" className="button" disabled={definition?.is_pinned === true} onClick={openForm}>
            {definition ? "Revisar comparación" : "Declarar comparación"}
          </button>
          {definition?.is_pinned === true && (
            <span className="muted small-text">
              {" "}
              La definición está fijada y no admite una nueva versión.
            </span>
          )}
        </div>
      )}

      {definition && (
        <ExperimentVariantsSection
          campaignId={campaignId}
          experiment={experiment}
          definition={definition}
          role={role}
          onChanged={onChanged}
        />
      )}

      {definition && (
        <MeasurementContractSection
          campaignId={campaignId}
          experiment={experiment}
          definition={definition}
          role={role}
          onChanged={onChanged}
        />
      )}

      {definition && (
        <ExecutionAuthorizationSection
          campaignId={campaignId}
          experiment={experiment}
          definition={definition}
          role={role}
          onChanged={onChanged}
        />
      )}

      {open && (
        <div className="panel" style={{ marginTop: 8 }}>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-type`}>Tipo de comparación</label>
            <select
              id={`${idPrefix}-type`}
              value={values.comparisonType}
              disabled={pending}
              onChange={(event) => update("comparisonType", event.target.value as ComparisonType)}
            >
              <option value="OBSERVATIONAL">{TYPE_COPY.OBSERVATIONAL}</option>
              <option value="CONTROLLED">{TYPE_COPY.CONTROLLED}</option>
            </select>
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-question`}>Pregunta de comparación</label>
            <textarea
              id={`${idPrefix}-question`}
              value={values.comparisonQuestion}
              disabled={pending}
              onChange={(event) => update("comparisonQuestion", event.target.value)}
            />
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-changed`}>Factor que cambia</label>
            <input
              id={`${idPrefix}-changed`}
              type="text"
              value={values.changedFactor}
              disabled={pending}
              onChange={(event) => update("changedFactor", event.target.value)}
            />
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-controlled`}>Factores controlados (uno por línea)</label>
            <textarea
              id={`${idPrefix}-controlled`}
              value={values.controlledFactors}
              disabled={pending}
              onChange={(event) => update("controlledFactors", event.target.value)}
            />
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-basis`}>Base de comparación</label>
            <textarea
              id={`${idPrefix}-basis`}
              value={values.comparisonBasis}
              disabled={pending}
              onChange={(event) => update("comparisonBasis", event.target.value)}
            />
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-scope`}>Alcance</label>
            <textarea
              id={`${idPrefix}-scope`}
              value={values.scope}
              disabled={pending}
              onChange={(event) => update("scope", event.target.value)}
            />
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-intent`}>Intención de aprendizaje</label>
            <textarea
              id={`${idPrefix}-intent`}
              value={values.learningIntent}
              disabled={pending}
              onChange={(event) => update("learningIntent", event.target.value)}
            />
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-boundary`}>Límite de conclusión (lo que esta comparación NO establece)</label>
            <textarea
              id={`${idPrefix}-boundary`}
              value={values.nonConclusionBoundary}
              disabled={pending}
              onChange={(event) => update("nonConclusionBoundary", event.target.value)}
            />
          </div>
          <p className="muted small-text">
            Se guarda como una nueva versión inmutable de la definición. El alcance es texto libre y no equivale a
            una audiencia gobernada. No crea variantes, mediciones ni ejecución.
          </p>
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button type="button" className="button primary" disabled={pending} onClick={submit}>
              {definition ? "Confirmar revisión" : "Confirmar comparación"}
            </button>
            <button type="button" className="button" disabled={pending} onClick={closeForm}>
              Cancelar
            </button>
          </div>
          {error && (
            <p role="alert" className="settings-feedback">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
