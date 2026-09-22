"use client";

// MVP-39: governed Measurement Contract — the PRE-EXECUTION declaration of
// how one Experiment's evidence is intended to be evaluated (frozen
// MVP-39A/-39B). Minimum reachable UI only: read the current tip (or "no
// contract declared"), and declare/revise a full-state form with a dynamic
// list of RequiredSignal rows. There is no freeze/execution/result control
// — none of those exist in MVP-39.
//
// MEASUREMENT CONTRACT != EVIDENCE != EVIDENCE BINDING != TRACKING
// IMPLEMENTATION != ALLOCATION != EXPOSURE != EXECUTION AUTHORIZATION !=
// EXPERIMENT RESULT != WINNER. Declaring or revising this Contract does not
// claim evidence exists, is bound, or is sufficient, and the copy below
// never does.
//
// Pre-Execution Measurement Declaration: the form can also declare a STRUCTURED declaration (descriptive or
// comparative, semantics version 1) — which metric/channel each signal is read from and the window — BEFORE
// execution starts. It is a DECLARATION: it is not pre-registration, does not validate or prove evidence and
// produces no eligibility, sufficiency, success or result. Starting execution freezes it permanently.
//
// Idempotency: `client_request_id` is generated once per section and KEPT
// across retryable failures (a lost response replays as a 200); it rotates
// only on a successful write or IDEMPOTENCY_KEY_CONFLICT. The pin
// (`definition_version_id`) is the id of the tip this section was rendered
// with. On VERSION_NOT_CURRENT / STRATEGY_STALE / BASE_STALE the draft is
// discarded and the view refetched — a stale draft is never silently
// rebased.

import { useEffect, useRef, useState } from "react";
import { declareMeasurementContract, getMeasurementContract } from "@/lib/api/strategy";
import { ApiError } from "@/lib/api/client";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type {
  ChannelBinding,
  DeclarationLevel,
  ExpectedDirection,
  ExperimentDefinitionPublic,
  ExperimentPublic,
  MeasurementContractPublic,
  RequiredSignalRequest,
} from "@/types/strategy";

const PROSE_MAX = 1000;
const SIGNAL_NAME_MAX = 200;
const SIGNAL_DESCRIPTION_MAX = 1000;
const BINDING_TEXT_MAX = 100;
// Semantics version 1 bounds (a rule of version 1, not a universal truth; legacy declarations are unchanged).
const STRUCTURED_WINDOW_MIN = 4;
const STRUCTURED_WINDOW_MAX = 3650;
const SEMANTICS_VERSION = 1;

type DeclarationMode = "LEGACY" | DeclarationLevel;

const MODE_COPY: Record<DeclarationMode, string> = {
  LEGACY: "Sin declaración estructurada (contrato tal como estaba)",
  DESCRIPTIVE: "Declaración descriptiva",
  COMPARATIVE: "Declaración comparativa",
};

const DIRECTION_COPY: Record<ExpectedDirection, string> = {
  INCREASE: "Aumento esperado",
  DECREASE: "Disminución esperada",
  TARGET: "Valor objetivo",
  NO_DIRECTION: "Sin dirección esperada",
};

interface SignalDraft {
  name: string;
  description: string;
  expectedDirection: ExpectedDirection | "";
  evidenceRequirement: string;
  trackingRequired: boolean;
  boundMetricName: string;
  channelBinding: ChannelBinding | "";
  boundChannel: string;
  minDataPoints: string;
}

const EMPTY_SIGNAL: SignalDraft = {
  name: "",
  description: "",
  expectedDirection: "",
  evidenceRequirement: "",
  trackingRequired: false,
  boundMetricName: "",
  channelBinding: "",
  boundChannel: "",
  minDataPoints: "",
};

interface FormValues {
  mode: DeclarationMode;
  baselineWindowDays: string;
  measurementWindowDays: string;
  minimumEvidence: string;
  successCriterion: string;
  analysisMethodIntent: string;
  stoppingRule: string;
  decisionRuleIntent: string;
  signals: SignalDraft[];
}

const EMPTY_FORM: FormValues = {
  mode: "LEGACY",
  baselineWindowDays: "",
  measurementWindowDays: "",
  minimumEvidence: "",
  successCriterion: "",
  analysisMethodIntent: "",
  stoppingRule: "",
  decisionRuleIntent: "",
  signals: [{ ...EMPTY_SIGNAL }],
};

// MVP-32A §R precedent: the same MEMBER+ tier that may propose an Experiment.
function canDeclareContract(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN" || role === "MEMBER";
}

function signalKey(value: string): string {
  return value.split(/\s+/).filter(Boolean).join(" ").toLowerCase();
}

function valuesFromContract(contract: MeasurementContractPublic | null): FormValues {
  if (!contract) return { ...EMPTY_FORM, signals: [{ ...EMPTY_SIGNAL }] };
  return {
    mode: contract.declaration_level ?? "LEGACY",
    baselineWindowDays: contract.baseline_window_days === null ? "" : String(contract.baseline_window_days),
    measurementWindowDays: contract.measurement_window_days === null ? "" : String(contract.measurement_window_days),
    minimumEvidence: contract.minimum_evidence ?? "",
    successCriterion: contract.success_criterion ?? "",
    analysisMethodIntent: contract.analysis_method_intent ?? "",
    stoppingRule: contract.stopping_rule ?? "",
    decisionRuleIntent: contract.decision_rule_intent ?? "",
    signals: contract.signals.map((signal) => ({
      name: signal.name,
      description: signal.description,
      expectedDirection: signal.expected_direction ?? "",
      evidenceRequirement: signal.evidence_requirement ?? "",
      trackingRequired: signal.tracking_required,
      boundMetricName: signal.bound_metric_name ?? "",
      channelBinding: signal.channel_binding ?? "",
      boundChannel: signal.bound_channel ?? "",
      minDataPoints: signal.min_data_points === null ? "" : String(signal.min_data_points),
    })),
  };
}

function isWholeNumber(value: string): boolean {
  const trimmed = value.trim();
  return trimmed.length > 0 && /^\d+$/.test(trimmed);
}

function validateStructured(values: FormValues, comparisonType: string): string | null {
  if (values.mode === "LEGACY") return null;
  if (comparisonType === "CONTROLLED") {
    return "Las declaraciones estructuradas no están disponibles para comparaciones controladas.";
  }
  const windowDays = Number(values.measurementWindowDays);
  if (!isWholeNumber(values.measurementWindowDays) || windowDays < STRUCTURED_WINDOW_MIN || windowDays > STRUCTURED_WINDOW_MAX) {
    return `Una declaración estructurada requiere una ventana de medición entre ${STRUCTURED_WINDOW_MIN} y ${STRUCTURED_WINDOW_MAX} días.`;
  }
  if (values.mode === "COMPARATIVE") {
    const baselineDays = Number(values.baselineWindowDays);
    if (
      !isWholeNumber(values.baselineWindowDays) ||
      baselineDays < STRUCTURED_WINDOW_MIN ||
      baselineDays > STRUCTURED_WINDOW_MAX
    ) {
      return `Una declaración comparativa requiere una ventana base entre ${STRUCTURED_WINDOW_MIN} y ${STRUCTURED_WINDOW_MAX} días.`;
    }
  }
  const slots = new Set<string>();
  const anyMetrics = new Set<string>();
  const exactMetrics = new Set<string>();
  for (const signal of values.signals) {
    const metric = signal.boundMetricName.trim();
    if (metric.length === 0) return 'Cada señal requiere una "Métrica vinculada".';
    if (metric.length > BINDING_TEXT_MAX || /[\r\n]/.test(metric)) {
      return `La métrica vinculada debe ser una sola línea de hasta ${BINDING_TEXT_MAX} caracteres.`;
    }
    if (signal.channelBinding === "") return "Cada señal requiere indicar si aplica a cualquier canal o a un canal exacto.";
    const channel = signal.boundChannel.trim();
    if (signal.channelBinding === "EXACT") {
      if (channel.length === 0) return 'Un canal exacto requiere indicar el "Canal".';
      if (channel.length > BINDING_TEXT_MAX || /[\r\n]/.test(channel)) {
        return `El canal debe ser una sola línea de hasta ${BINDING_TEXT_MAX} caracteres.`;
      }
    }
    if (values.mode === "DESCRIPTIVE") {
      if (!isWholeNumber(signal.minDataPoints) || Number(signal.minDataPoints) < 1) {
        return 'Cada señal de una declaración descriptiva requiere "Mínimo de datos" (entero mayor que cero).';
      }
    }
    const slot = `${metric}|${signal.channelBinding}|${signal.channelBinding === "EXACT" ? channel : ""}`;
    if (slots.has(slot)) return "Dos señales no pueden declarar la misma métrica y el mismo canal.";
    slots.add(slot);
    (signal.channelBinding === "ANY" ? anyMetrics : exactMetrics).add(metric);
    if (anyMetrics.has(metric) && exactMetrics.has(metric)) {
      return "Una métrica no puede vincularse a la vez a cualquier canal y a un canal exacto.";
    }
  }
  return null;
}

function validateForm(values: FormValues, comparisonType: string): string | null {
  if (values.mode === "LEGACY" && values.measurementWindowDays.trim().length > 0) {
    const parsed = Number(values.measurementWindowDays);
    if (!Number.isInteger(parsed) || parsed < 1) {
      return 'El campo "Ventana de medición (días)" debe ser un número entero mayor que cero.';
    }
  }
  for (const [name, value] of [
    ["Evidencia mínima esperada", values.minimumEvidence],
    ["Criterio de éxito", values.successCriterion],
    ["Intención de análisis", values.analysisMethodIntent],
    ["Regla de corte", values.stoppingRule],
    ["Intención de la regla de decisión", values.decisionRuleIntent],
  ] as const) {
    if (value.trim().length > PROSE_MAX) return `El campo "${name}" no puede superar ${PROSE_MAX} caracteres.`;
  }
  if (comparisonType === "CONTROLLED" && values.successCriterion.trim().length === 0) {
    return "Una comparación controlada requiere declarar un criterio de éxito.";
  }
  if (values.signals.length === 0) {
    return "Debe declarar al menos una señal requerida.";
  }
  const structured = validateStructured(values, comparisonType);
  if (structured) return structured;
  const seen = new Set<string>();
  for (const signal of values.signals) {
    const name = signal.name.trim();
    const description = signal.description.trim();
    if (name.length === 0) return 'Cada señal requiere un "Nombre".';
    if (name.length > SIGNAL_NAME_MAX) return `El nombre de la señal no puede superar ${SIGNAL_NAME_MAX} caracteres.`;
    if (description.length === 0) return 'Cada señal requiere una "Descripción".';
    if (description.length > SIGNAL_DESCRIPTION_MAX) {
      return `La descripción de la señal no puede superar ${SIGNAL_DESCRIPTION_MAX} caracteres.`;
    }
    const key = signalKey(name);
    if (seen.has(key)) return "Los nombres de las señales no pueden repetirse.";
    seen.add(key);
  }
  return null;
}

function describeContractError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "MEASUREMENT_CONTRACT_DEFINITION_VERSION_NOT_CURRENT":
        return "La definición cambió mientras editabas el contrato de medición. Se actualizó la vista; revísala y vuelve a intentar.";
      case "MEASUREMENT_CONTRACT_STRATEGY_STALE":
        return "Este experimento pertenece a una versión de la estrategia que ya no es la vigente, por lo que no admite un nuevo contrato de medición.";
      case "MEASUREMENT_CONTRACT_BASE_STALE":
        return "El contrato de medición cambió mientras lo editabas. Se actualizó la vista con la versión vigente; revísala y vuelve a intentar.";
      case "MEASUREMENT_CONTRACT_FROZEN_BY_AUTHORIZATION":
        return "El contrato de medición está congelado por una autorización de ejecución activa. Revoca esa autorización para poder revisarlo.";
      case "MEASUREMENT_CONTRACT_FROZEN_BY_EXECUTION_START":
        return "El contrato de medición está congelado de forma permanente porque se atestiguó el inicio de la ejecución. Revocar la autorización no lo reabre; para corregirlo hay que crear un nuevo experimento.";
      case "MEASUREMENT_CONTRACT_UNCHANGED":
        return "No hay cambios respecto al contrato de medición vigente.";
      case "MEASUREMENT_CONTRACT_SUCCESS_CRITERION_REQUIRED":
        return "Una comparación controlada requiere declarar un criterio de éxito.";
      case "MEASUREMENT_CONTRACT_DECLARATION_INVALID":
        return "La declaración estructurada está incompleta o es inconsistente. Revisa las ventanas, las métricas vinculadas y los canales.";
      case "MEASUREMENT_CONTRACT_CONTROLLED_DECLARATION_NOT_SUPPORTED":
        return "Las declaraciones estructuradas no están disponibles para comparaciones controladas.";
      case "MEASUREMENT_CONTRACT_BINDING_CONFLICT":
        return "Las señales declaran vínculos de métrica y canal en conflicto: una misma métrica no puede vincularse a cualquier canal y a un canal exacto, ni repetirse en el mismo canal.";
      case "IDEMPOTENCY_KEY_CONFLICT":
        return "Esta solicitud no coincide con un envío anterior. Revisa los datos e inténtalo de nuevo.";
    }
  }
  return describeCampaignError(error);
}

export function MeasurementContractSection({
  campaignId,
  experiment,
  definition,
  role,
  onChanged,
}: {
  campaignId: string;
  experiment: ExperimentPublic;
  definition: ExperimentDefinitionPublic;
  role: string | null;
  onChanged: () => void;
}) {
  const [contract, setContract] = useState<MeasurementContractPublic | null>(null);
  const [listError, setListError] = useState("");
  const [reloadToken, setReloadToken] = useState(0);

  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<FormValues>({ ...EMPTY_FORM });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  // Lazy state initializer: the initial key is generated exactly once.
  const [initialClientRequestId] = useState(() => crypto.randomUUID());
  const clientRequestIdRef = useRef(initialClientRequestId);
  const idPrefix = `measurement-contract-${experiment.id}`;

  // Fetch only when the definition reports a declared Contract — a
  // Contract-less definition never fetched (and nothing is rendered from
  // `contract` in that case either), mirroring the Variant section's own
  // skip-when-empty discipline. `has_measurement_contract` only ever goes
  // false -> true (Contracts are never un-declared), so no reset is needed.
  useEffect(() => {
    if (!definition.has_measurement_contract) return;
    let cancelled = false;
    getMeasurementContract(campaignId, experiment.id)
      .then((current) => {
        if (!cancelled) {
          setContract(current);
          setListError("");
        }
      })
      .catch((caught) => {
        if (!cancelled) setListError(describeCampaignError(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, experiment.id, definition.id, definition.has_measurement_contract, definition.measurement_contract_version, reloadToken]);

  function openForm() {
    setValues(valuesFromContract(contract));
    setError("");
    setOpen(true);
  }

  function closeForm() {
    setOpen(false);
    setValues({ ...EMPTY_FORM });
  }

  function updateSignal(index: number, patch: Partial<SignalDraft>) {
    setValues((current) => ({
      ...current,
      signals: current.signals.map((signal, i) => (i === index ? { ...signal, ...patch } : signal)),
    }));
  }

  function addSignal() {
    setValues((current) => ({ ...current, signals: [...current.signals, { ...EMPTY_SIGNAL }] }));
  }

  function removeSignal(index: number) {
    setValues((current) => ({ ...current, signals: current.signals.filter((_signal, i) => i !== index) }));
  }

  async function submit() {
    if (pending) return;
    const invalid = validateForm(values, definition.comparison_type);
    if (invalid) {
      setError(invalid);
      return;
    }
    setPending(true);
    setError("");
    try {
      const structured = values.mode !== "LEGACY";
      const signals: RequiredSignalRequest[] = values.signals.map((signal) => ({
        name: signal.name.trim(),
        description: signal.description.trim(),
        expected_direction: signal.expectedDirection === "" ? null : signal.expectedDirection,
        evidence_requirement: signal.evidenceRequirement.trim().length > 0 ? signal.evidenceRequirement.trim() : null,
        tracking_required: signal.trackingRequired,
        // A LEGACY declaration sends none of the binding fields, exactly as before.
        ...(structured
          ? {
              bound_metric_name: signal.boundMetricName.trim(),
              channel_binding: signal.channelBinding === "" ? null : signal.channelBinding,
              bound_channel: signal.channelBinding === "EXACT" ? signal.boundChannel.trim() : null,
              min_data_points: values.mode === "DESCRIPTIVE" ? Number(signal.minDataPoints) : null,
            }
          : {}),
      }));
      await declareMeasurementContract(campaignId, experiment.id, {
        base_version: contract?.version ?? 0,
        client_request_id: clientRequestIdRef.current,
        definition_version_id: definition.id,
        measurement_window_days:
          values.measurementWindowDays.trim().length > 0 ? Number(values.measurementWindowDays) : null,
        minimum_evidence: values.minimumEvidence.trim().length > 0 ? values.minimumEvidence.trim() : null,
        success_criterion: values.successCriterion.trim().length > 0 ? values.successCriterion.trim() : null,
        analysis_method_intent:
          values.analysisMethodIntent.trim().length > 0 ? values.analysisMethodIntent.trim() : null,
        stopping_rule: values.stoppingRule.trim().length > 0 ? values.stoppingRule.trim() : null,
        decision_rule_intent: values.decisionRuleIntent.trim().length > 0 ? values.decisionRuleIntent.trim() : null,
        ...(structured
          ? {
              declaration_level: values.mode as DeclarationLevel,
              declaration_semantics_version: SEMANTICS_VERSION,
              baseline_window_days: values.mode === "COMPARATIVE" ? Number(values.baselineWindowDays) : null,
            }
          : {}),
        signals,
      });
      clientRequestIdRef.current = crypto.randomUUID();
      closeForm();
      setReloadToken((token) => token + 1);
      onChanged();
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : null;
      if (code === "IDEMPOTENCY_KEY_CONFLICT") {
        clientRequestIdRef.current = crypto.randomUUID();
      }
      setError(describeContractError(caught));
      if (
        code === "MEASUREMENT_CONTRACT_DEFINITION_VERSION_NOT_CURRENT" ||
        code === "MEASUREMENT_CONTRACT_STRATEGY_STALE" ||
        code === "MEASUREMENT_CONTRACT_BASE_STALE"
      ) {
        // Never silently rebase a stale draft: discard it and refetch.
        closeForm();
        onChanged();
        setReloadToken((token) => token + 1);
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div style={{ marginTop: 12 }}>
      <p className="muted small-text">
        <strong>Contrato de medición</strong>
        {contract ? ` · versión ${contract.version}` : ""}
      </p>

      {!contract ? (
        <p className="muted small-text">No se ha declarado un contrato de medición para esta versión de la definición.</p>
      ) : (
        <dl className="small-text" style={{ marginTop: 4 }}>
          <dt>Declaración de medición</dt>
          <dd>
            {contract.declaration_level === null
              ? "Sin declaración estructurada (heredado): describe la intención, pero no fija qué métrica ni qué canal se leerán."
              : `${MODE_COPY[contract.declaration_level]} · semántica versión ${contract.declaration_semantics_version}`}
          </dd>
          {contract.measurement_window_days !== null && (
            <>
              <dt>Ventana de medición</dt>
              <dd>{contract.measurement_window_days} días</dd>
            </>
          )}
          {contract.baseline_window_days !== null && (
            <>
              <dt>Ventana base</dt>
              <dd>{contract.baseline_window_days} días</dd>
            </>
          )}
          {contract.minimum_evidence && (
            <>
              <dt>Evidencia mínima esperada</dt>
              <dd style={{ whiteSpace: "pre-wrap" }}>{contract.minimum_evidence}</dd>
            </>
          )}
          {contract.success_criterion && (
            <>
              <dt>Criterio de éxito</dt>
              <dd style={{ whiteSpace: "pre-wrap" }}>{contract.success_criterion}</dd>
            </>
          )}
          {contract.analysis_method_intent && (
            <>
              <dt>Intención de análisis</dt>
              <dd style={{ whiteSpace: "pre-wrap" }}>{contract.analysis_method_intent}</dd>
            </>
          )}
          {contract.stopping_rule && (
            <>
              <dt>Regla de corte</dt>
              <dd style={{ whiteSpace: "pre-wrap" }}>{contract.stopping_rule}</dd>
            </>
          )}
          {contract.decision_rule_intent && (
            <>
              <dt>Intención de la regla de decisión</dt>
              <dd style={{ whiteSpace: "pre-wrap" }}>{contract.decision_rule_intent}</dd>
            </>
          )}
          <dt>Señales requeridas</dt>
          <dd>
            <ol style={{ margin: 0, paddingLeft: 18 }}>
              {contract.signals.map((signal) => (
                <li key={signal.id}>
                  <strong>{signal.name}</strong>
                  <span style={{ whiteSpace: "pre-wrap", display: "block" }}>{signal.description}</span>
                  {signal.expected_direction && (
                    <span className="muted">{DIRECTION_COPY[signal.expected_direction]}</span>
                  )}
                  {signal.evidence_requirement && (
                    <span style={{ whiteSpace: "pre-wrap", display: "block" }}>{signal.evidence_requirement}</span>
                  )}
                  {signal.bound_metric_name !== null && (
                    <span className="muted" style={{ display: "block" }}>
                      Métrica declarada «{signal.bound_metric_name}» ·{" "}
                      {signal.channel_binding === "EXACT" ? `canal exacto «${signal.bound_channel}»` : "cualquier canal"}
                      {signal.min_data_points !== null ? ` · mínimo de datos: ${signal.min_data_points}` : ""}
                    </span>
                  )}
                  {signal.tracking_required && <span className="muted"> · requiere seguimiento (declarativo)</span>}
                </li>
              ))}
            </ol>
          </dd>
        </dl>
      )}

      {listError && (
        <p role="alert" className="settings-feedback">
          {listError}
        </p>
      )}

      <p className="muted small-text" style={{ marginTop: 4 }}>
        Declarar o revisar este contrato describe cómo se intenta evaluar la evidencia. No declara que la evidencia
        exista, esté vinculada o sea suficiente, y no autoriza asignación, exposición ni ejecución.
      </p>

      {error && !open && (
        <p role="alert" className="settings-feedback">
          {error}
        </p>
      )}

      {canDeclareContract(role) && !open && (
        <div className="settings-form-actions" style={{ marginTop: 8 }}>
          <button type="button" className="button" onClick={openForm}>
            {contract ? "Revisar contrato de medición" : "Declarar contrato de medición"}
          </button>
        </div>
      )}

      {open && (
        <div className="panel" style={{ marginTop: 8 }}>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-mode`}>Tipo de declaración de medición</label>
            <select
              id={`${idPrefix}-mode`}
              value={values.mode}
              disabled={pending}
              onChange={(event) => setValues((current) => ({ ...current, mode: event.target.value as DeclarationMode }))}
            >
              <option value="LEGACY">{MODE_COPY.LEGACY}</option>
              <option value="DESCRIPTIVE" disabled={definition.comparison_type === "CONTROLLED"}>
                {MODE_COPY.DESCRIPTIVE}
              </option>
              <option value="COMPARATIVE" disabled={definition.comparison_type === "CONTROLLED"}>
                {MODE_COPY.COMPARATIVE}
              </option>
            </select>
            {definition.comparison_type === "CONTROLLED" && (
              <p className="muted small-text">
                Las declaraciones estructuradas no están disponibles para comparaciones controladas.
              </p>
            )}
          </div>
          {values.mode !== "LEGACY" && (
            <div className="muted small-text" data-testid="declaration-explanation" style={{ marginBottom: 8 }}>
              <p>
                Esta declaración fija, antes de ejecutar, cómo una medición futura leería la evidencia. Es una
                declaración: no valida ni prueba la evidencia y no establece atribución ni causalidad.
              </p>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                <li>La métrica y el canal se comparan de forma exacta y distinguiendo mayúsculas de minúsculas.</li>
                <li>
                  La ventana se cuenta en periodos de 24 horas transcurridas desde el inicio atestiguado de la ejecución.
                  El mínimo estructurado es de {STRUCTURED_WINDOW_MIN} días.
                </li>
                <li>
                  Las fechas límite ambiguas (cuando no se puede saber de qué lado del inicio caen) serán excluidas por la
                  medición futura y se informará de ello.
                </li>
                <li>Si una señal aplica a cualquier canal, cada canal se evaluará por separado.</li>
                {values.mode === "COMPARATIVE" && (
                  <li>
                    La versión 1 de la declaración comparativa admite un dato por lado (base y observación) en cada canal,
                    con periodos de igual duración y sin emparejar entre canales.
                  </li>
                )}
                <li>
                  Al atestiguar el inicio de la ejecución, esta declaración queda congelada de forma permanente; cambiarla
                  después exigiría un nuevo experimento. Las ejecuciones ya iniciadas no se pueden reconvertir.
                </li>
              </ul>
            </div>
          )}
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-window`}>
              {values.mode === "LEGACY"
                ? "Ventana de medición (días, opcional)"
                : `Ventana de medición (días, entre ${STRUCTURED_WINDOW_MIN} y ${STRUCTURED_WINDOW_MAX})`}
            </label>
            <input
              id={`${idPrefix}-window`}
              type="number"
              min={values.mode === "LEGACY" ? 1 : STRUCTURED_WINDOW_MIN}
              value={values.measurementWindowDays}
              disabled={pending}
              onChange={(event) => setValues((current) => ({ ...current, measurementWindowDays: event.target.value }))}
            />
          </div>
          {values.mode === "COMPARATIVE" && (
            <div className="settings-field">
              <label htmlFor={`${idPrefix}-baseline`}>
                Ventana base (días, entre {STRUCTURED_WINDOW_MIN} y {STRUCTURED_WINDOW_MAX})
              </label>
              <input
                id={`${idPrefix}-baseline`}
                type="number"
                min={STRUCTURED_WINDOW_MIN}
                value={values.baselineWindowDays}
                disabled={pending}
                onChange={(event) => setValues((current) => ({ ...current, baselineWindowDays: event.target.value }))}
              />
            </div>
          )}
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-evidence`}>Evidencia mínima esperada (opcional)</label>
            <textarea
              id={`${idPrefix}-evidence`}
              value={values.minimumEvidence}
              disabled={pending}
              onChange={(event) => setValues((current) => ({ ...current, minimumEvidence: event.target.value }))}
            />
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-success`}>
              Criterio de éxito {definition.comparison_type === "CONTROLLED" ? "" : "(opcional)"}
            </label>
            <textarea
              id={`${idPrefix}-success`}
              value={values.successCriterion}
              disabled={pending}
              onChange={(event) => setValues((current) => ({ ...current, successCriterion: event.target.value }))}
            />
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-method`}>Intención de análisis (opcional)</label>
            <textarea
              id={`${idPrefix}-method`}
              value={values.analysisMethodIntent}
              disabled={pending}
              onChange={(event) => setValues((current) => ({ ...current, analysisMethodIntent: event.target.value }))}
            />
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-stopping`}>Regla de corte (opcional)</label>
            <textarea
              id={`${idPrefix}-stopping`}
              value={values.stoppingRule}
              disabled={pending}
              onChange={(event) => setValues((current) => ({ ...current, stoppingRule: event.target.value }))}
            />
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-decision`}>Intención de la regla de decisión (opcional)</label>
            <textarea
              id={`${idPrefix}-decision`}
              value={values.decisionRuleIntent}
              disabled={pending}
              onChange={(event) => setValues((current) => ({ ...current, decisionRuleIntent: event.target.value }))}
            />
          </div>

          <p className="muted small-text" style={{ marginTop: 8 }}>
            <strong>Señales requeridas</strong>
          </p>
          {values.signals.map((signal, index) => (
            <div key={index} className="panel" style={{ marginTop: 4 }}>
              <div className="settings-field">
                <label htmlFor={`${idPrefix}-signal-${index}-name`}>Nombre</label>
                <input
                  id={`${idPrefix}-signal-${index}-name`}
                  type="text"
                  value={signal.name}
                  disabled={pending}
                  onChange={(event) => updateSignal(index, { name: event.target.value })}
                />
              </div>
              <div className="settings-field">
                <label htmlFor={`${idPrefix}-signal-${index}-description`}>Descripción</label>
                <textarea
                  id={`${idPrefix}-signal-${index}-description`}
                  value={signal.description}
                  disabled={pending}
                  onChange={(event) => updateSignal(index, { description: event.target.value })}
                />
              </div>
              <div className="settings-field">
                <label htmlFor={`${idPrefix}-signal-${index}-direction`}>Dirección esperada (opcional)</label>
                <select
                  id={`${idPrefix}-signal-${index}-direction`}
                  value={signal.expectedDirection}
                  disabled={pending}
                  onChange={(event) => updateSignal(index, { expectedDirection: event.target.value as ExpectedDirection | "" })}
                >
                  <option value="">Sin declarar</option>
                  <option value="INCREASE">{DIRECTION_COPY.INCREASE}</option>
                  <option value="DECREASE">{DIRECTION_COPY.DECREASE}</option>
                  <option value="TARGET">{DIRECTION_COPY.TARGET}</option>
                  <option value="NO_DIRECTION">{DIRECTION_COPY.NO_DIRECTION}</option>
                </select>
              </div>
              <div className="settings-field">
                <label htmlFor={`${idPrefix}-signal-${index}-evidence`}>Requisito de evidencia (opcional)</label>
                <textarea
                  id={`${idPrefix}-signal-${index}-evidence`}
                  value={signal.evidenceRequirement}
                  disabled={pending}
                  onChange={(event) => updateSignal(index, { evidenceRequirement: event.target.value })}
                />
              </div>
              {values.mode !== "LEGACY" && (
                <>
                  <div className="settings-field">
                    <label htmlFor={`${idPrefix}-signal-${index}-metric`}>Métrica vinculada</label>
                    <input
                      id={`${idPrefix}-signal-${index}-metric`}
                      type="text"
                      value={signal.boundMetricName}
                      disabled={pending}
                      onChange={(event) => updateSignal(index, { boundMetricName: event.target.value })}
                    />
                  </div>
                  <div className="settings-field">
                    <label htmlFor={`${idPrefix}-signal-${index}-binding`}>Canal</label>
                    <select
                      id={`${idPrefix}-signal-${index}-binding`}
                      value={signal.channelBinding}
                      disabled={pending}
                      onChange={(event) =>
                        updateSignal(index, {
                          channelBinding: event.target.value as ChannelBinding | "",
                          boundChannel: event.target.value === "EXACT" ? signal.boundChannel : "",
                        })
                      }
                    >
                      <option value="">Sin declarar</option>
                      <option value="ANY">Cualquier canal (cada canal por separado)</option>
                      <option value="EXACT">Un canal exacto</option>
                    </select>
                  </div>
                  {signal.channelBinding === "EXACT" && (
                    <div className="settings-field">
                      <label htmlFor={`${idPrefix}-signal-${index}-channel`}>Nombre exacto del canal</label>
                      <input
                        id={`${idPrefix}-signal-${index}-channel`}
                        type="text"
                        value={signal.boundChannel}
                        disabled={pending}
                        onChange={(event) => updateSignal(index, { boundChannel: event.target.value })}
                      />
                    </div>
                  )}
                  {values.mode === "DESCRIPTIVE" && (
                    <div className="settings-field">
                      <label htmlFor={`${idPrefix}-signal-${index}-minpoints`}>Mínimo de datos</label>
                      <input
                        id={`${idPrefix}-signal-${index}-minpoints`}
                        type="number"
                        min={1}
                        value={signal.minDataPoints}
                        disabled={pending}
                        onChange={(event) => updateSignal(index, { minDataPoints: event.target.value })}
                      />
                    </div>
                  )}
                </>
              )}
              <div className="settings-field">
                <label htmlFor={`${idPrefix}-signal-${index}-tracking`}>
                  <input
                    id={`${idPrefix}-signal-${index}-tracking`}
                    type="checkbox"
                    checked={signal.trackingRequired}
                    disabled={pending}
                    onChange={(event) => updateSignal(index, { trackingRequired: event.target.checked })}
                  />{" "}
                  Requiere seguimiento (solo declarativo; no implica que el seguimiento exista o esté validado)
                </label>
              </div>
              {values.signals.length > 1 && (
                <div className="settings-form-actions">
                  <button type="button" className="button" disabled={pending} onClick={() => removeSignal(index)}>
                    Quitar señal
                  </button>
                </div>
              )}
            </div>
          ))}
          <div className="settings-form-actions" style={{ marginTop: 4 }}>
            <button type="button" className="button" disabled={pending} onClick={addSignal}>
              Agregar señal
            </button>
          </div>

          <p className="muted small-text" style={{ marginTop: 8 }}>
            Se guarda como una nueva versión inmutable del contrato de medición. No crea ni valida seguimiento, no
            vincula evidencia y no autoriza asignación, exposición ni ejecución.
          </p>
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button type="button" className="button primary" disabled={pending} onClick={submit}>
              {contract ? "Confirmar revisión" : "Confirmar contrato"}
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
