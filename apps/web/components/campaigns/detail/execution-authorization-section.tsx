"use client";

// MVP-40: governed Execution Authorization (frozen Execution Authorization
// Discovery/Design Freeze). Minimum reachable UI only: show the current
// ACTIVE authorization (or none), the dependency checklist, the history, an
// Authorize form (unit of assignment + allocation design, both declared
// intent only) and a Revoke form (reason required).
//
// AUTHORIZATION != EXECUTION != ASSIGNMENT != EXPOSURE != TRACKING
// VALIDATION != MEASUREMENT != RESULT != WINNER != VALIDITY != CAUSALITY.
// Authorizing pins the configuration that is current on the server — the
// client never names the Definition, Contract or Variants — and the copy
// below never claims that anything was executed, assigned, exposed, tracked
// or measured. There is no execute/assign/expose/result/winner control.
//
// Idempotency: `client_request_id` is generated once per section and KEPT
// across retryable failures (a lost response replays as a 200); it rotates
// only on a successful write or IDEMPOTENCY_KEY_CONFLICT (a re-authorization
// needs a fresh key by design).
//
// Governed Execution Start: an active member can ATTEST that execution of the
// active authorization began at a stated instant. This is a HUMAN DECLARATION —
// the system never verifies it externally and it is never shown as a fact about
// the outside world. It does not mean assignment, delivery, exposure, evidence,
// a result or validity. It cannot be corrected and permanently freezes the
// measurement contract lineage and further condition declaration. The attested
// instant (`started_at`) and the server record time (`created_at`) are always
// displayed separately. There is no stop/complete/assign/expose control.

import { useEffect, useRef, useState } from "react";
import {
  authorizeExecution,
  getExecutionAuthorization,
  getExecutionAuthorizationHistory,
  revokeExecutionAuthorization,
  startExecution,
} from "@/lib/api/strategy";
import { ApiError } from "@/lib/api/client";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type {
  ExecutionAuthorizationPublic,
  ExperimentDefinitionPublic,
  ExperimentPublic,
} from "@/types/strategy";
import { EvidenceClaimsSection } from "./evidence-claims-section";

const UNIT_MAX = 200;
const DESIGN_MAX = 2000;
const REASON_MAX = 1000;

// The same MEMBER+ tier as Definition/Variant/Contract (frozen §20).
function canAuthorize(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN" || role === "MEMBER";
}

function minimumVariants(comparisonType: string): number {
  return comparisonType === "CONTROLLED" ? 2 : 1;
}

function describeAuthorizationError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "EXECUTION_AUTHORIZATION_STRATEGY_STALE":
        return "Este experimento pertenece a una versión de la estrategia que ya no es la vigente, por lo que no puede autorizarse una nueva configuración.";
      case "EXECUTION_AUTHORIZATION_NO_MEASUREMENT_CONTRACT":
        return "Este experimento no tiene un contrato de medición y no puede autorizarse.";
      case "EXECUTION_AUTHORIZATION_INSUFFICIENT_VARIANTS":
        return "Este experimento no tiene suficientes condiciones declaradas para ser autorizado.";
      case "EXECUTION_AUTHORIZATION_NONE_ACTIVE":
        return "No hay una autorización activa que revocar. Se actualizó la vista.";
      case "EXECUTION_AUTHORIZATION_ACTIVE_STARTED":
        return "La autorización activa ya tiene un inicio atestiguado. Revócala explícitamente antes de autorizar de nuevo.";
      case "EXECUTION_START_AUTHORIZATION_NOT_ACTIVE":
        return "Esta autorización ya no está activa, por lo que no puede registrarse su inicio. Se actualizó la vista.";
      case "EXECUTION_START_ALREADY_STARTED":
        return "Esta autorización ya tiene un inicio atestiguado. Se actualizó la vista.";
      case "EXECUTION_START_AUTHORIZATION_STALE":
        return "La configuración del experimento cambió después de autorizarla (por ejemplo, se declaró otra condición). Autoriza de nuevo antes de registrar el inicio.";
      case "EXECUTION_START_TIME_INVALID":
        return "La fecha y hora del inicio no son admisibles: no pueden ser anteriores a la autorización ni estar en el futuro.";
      case "IDEMPOTENCY_KEY_CONFLICT":
        return "Esta solicitud no coincide con un envío anterior o la configuración vigente cambió. Revisa los datos e inténtalo de nuevo.";
    }
  }
  return describeCampaignError(error);
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString("es");
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

// `datetime-local` value (local time, second precision) for an instant.
function toLocalInputValue(instant: Date): string {
  return (
    `${instant.getFullYear()}-${pad(instant.getMonth() + 1)}-${pad(instant.getDate())}` +
    `T${pad(instant.getHours())}:${pad(instant.getMinutes())}:${pad(instant.getSeconds())}`
  );
}

// Default attested instant: max(now, authorization creation), rounded UP to the next whole second so the
// server's strict "not before the authorization" floor is never missed by input truncation.
function defaultStartInput(authorizationCreatedAt: string): string {
  const base = Math.max(Date.now(), new Date(authorizationCreatedAt).getTime());
  return toLocalInputValue(new Date(Math.ceil(base / 1000) * 1000));
}

export function ExecutionAuthorizationSection({
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
  const [current, setCurrent] = useState<ExecutionAuthorizationPublic | null>(null);
  const [history, setHistory] = useState<ExecutionAuthorizationPublic[]>([]);
  const [listError, setListError] = useState("");
  const [reloadToken, setReloadToken] = useState(0);

  const [mode, setMode] = useState<"idle" | "authorize" | "revoke" | "start">("idle");
  const [startedAtInput, setStartedAtInput] = useState("");
  const [startConfirmed, setStartConfirmed] = useState(false);
  const [unit, setUnit] = useState("");
  const [design, setDesign] = useState("");
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  // Lazy state initializer: the initial key is generated exactly once.
  const [initialClientRequestId] = useState(() => crypto.randomUUID());
  const clientRequestIdRef = useRef(initialClientRequestId);
  // Start idempotency: the key is kept across retries of the SAME attested instant and rotated when the
  // instant changes (the material is the authorization plus started_at) or on success/conflict.
  const startAttemptRef = useRef<{ key: string; startedAt: string } | null>(null);
  const idPrefix = `execution-authorization-${experiment.id}`;

  const requiredVariants = minimumVariants(definition.comparison_type);
  const hasContract = definition.has_measurement_contract;
  const hasVariants = definition.variant_count >= requiredVariants;

  // An authorization can only exist once the Definition is pinned (a Variant
  // or a Contract exists); before that there is nothing to fetch.
  useEffect(() => {
    if (!definition.is_pinned) return;
    let cancelled = false;
    Promise.all([
      getExecutionAuthorization(campaignId, experiment.id),
      getExecutionAuthorizationHistory(campaignId, experiment.id),
    ])
      .then(([active, all]) => {
        if (!cancelled) {
          setCurrent(active);
          setHistory(all.authorizations);
          setListError("");
        }
      })
      .catch((caught) => {
        if (!cancelled) setListError(describeCampaignError(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, experiment.id, definition.id, definition.is_pinned, definition.variant_count, definition.measurement_contract_version, reloadToken]);

  function openAuthorize() {
    setUnit(current?.unit_of_assignment ?? "");
    setDesign(current?.allocation_design ?? "");
    setError("");
    setMode("authorize");
  }

  function openRevoke() {
    setReason("");
    setError("");
    setMode("revoke");
  }

  function openStart() {
    if (!current) return;
    setStartedAtInput(defaultStartInput(current.created_at));
    setStartConfirmed(false);
    setError("");
    setMode("start");
  }

  function close() {
    setMode("idle");
    setUnit("");
    setDesign("");
    setReason("");
    setStartedAtInput("");
    setStartConfirmed(false);
  }

  async function submitAuthorize() {
    if (pending) return;
    const trimmedUnit = unit.trim();
    const trimmedDesign = design.trim();
    if (trimmedUnit.length === 0) {
      setError('El campo "Unidad de asignación" es obligatorio.');
      return;
    }
    if (trimmedUnit.length > UNIT_MAX) {
      setError(`La unidad de asignación no puede superar ${UNIT_MAX} caracteres.`);
      return;
    }
    if (trimmedUnit.includes("\n")) {
      setError("La unidad de asignación debe ser una sola línea.");
      return;
    }
    if (trimmedDesign.length === 0) {
      setError('El campo "Diseño de asignación" es obligatorio.');
      return;
    }
    if (trimmedDesign.length > DESIGN_MAX) {
      setError(`El diseño de asignación no puede superar ${DESIGN_MAX} caracteres.`);
      return;
    }
    setPending(true);
    setError("");
    try {
      await authorizeExecution(campaignId, experiment.id, {
        client_request_id: clientRequestIdRef.current,
        unit_of_assignment: trimmedUnit,
        allocation_design: trimmedDesign,
      });
      clientRequestIdRef.current = crypto.randomUUID();
      close();
      setReloadToken((token) => token + 1);
      onChanged();
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : null;
      if (code === "IDEMPOTENCY_KEY_CONFLICT") {
        clientRequestIdRef.current = crypto.randomUUID();
      }
      setError(describeAuthorizationError(caught));
      if (code === "EXECUTION_AUTHORIZATION_STRATEGY_STALE") {
        // The Strategy moved on: discard the draft and refetch, never rebase.
        close();
        onChanged();
        setReloadToken((token) => token + 1);
      }
    } finally {
      setPending(false);
    }
  }

  async function submitRevoke() {
    if (pending) return;
    const trimmed = reason.trim();
    if (trimmed.length === 0) {
      setError("Debes indicar el motivo de la revocación.");
      return;
    }
    if (trimmed.length > REASON_MAX) {
      setError(`El motivo no puede superar ${REASON_MAX} caracteres.`);
      return;
    }
    setPending(true);
    setError("");
    try {
      await revokeExecutionAuthorization(campaignId, experiment.id, { reason: trimmed });
      close();
      setReloadToken((token) => token + 1);
      onChanged();
    } catch (caught) {
      setError(describeAuthorizationError(caught));
      if (caught instanceof ApiError && caught.code === "EXECUTION_AUTHORIZATION_NONE_ACTIVE") {
        close();
        setReloadToken((token) => token + 1);
      }
    } finally {
      setPending(false);
    }
  }

  async function submitStart() {
    if (pending || !current) return;
    const attested = new Date(startedAtInput);
    if (startedAtInput.trim().length === 0 || Number.isNaN(attested.getTime())) {
      setError("Indica la fecha y hora en que comenzó la ejecución.");
      return;
    }
    if (!startConfirmed) {
      setError("Debes confirmar que entiendes las consecuencias de declarar el inicio.");
      return;
    }
    const startedAt = attested.toISOString();
    if (startAttemptRef.current === null || startAttemptRef.current.startedAt !== startedAt) {
      startAttemptRef.current = { key: crypto.randomUUID(), startedAt };
    }
    setPending(true);
    setError("");
    try {
      await startExecution(campaignId, experiment.id, current.id, {
        client_request_id: startAttemptRef.current.key,
        started_at: startedAt,
      });
      startAttemptRef.current = null;
      close();
      setReloadToken((token) => token + 1);
      onChanged();
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : null;
      if (code === "IDEMPOTENCY_KEY_CONFLICT") {
        startAttemptRef.current = null;
      }
      setError(describeAuthorizationError(caught));
      if (
        code === "EXECUTION_START_AUTHORIZATION_NOT_ACTIVE" ||
        code === "EXECUTION_START_ALREADY_STARTED" ||
        code === "EXECUTION_START_AUTHORIZATION_STALE"
      ) {
        // The state moved on: discard the draft and refetch, never retry blindly.
        startAttemptRef.current = null;
        close();
        onChanged();
        setReloadToken((token) => token + 1);
      }
    } finally {
      setPending(false);
    }
  }

  const past = history.filter((entry) => entry.id !== current?.id);
  const started = current?.execution_start ?? null;
  // Experiment Evidence Binding: claims belong to a STARTED attempt, not to an active Authorization — a late
  // claim after revocation is allowed (R2), so the most recent started attempt stays claimable and readable.
  const claimsTarget = started
    ? current
    : ([...history].reverse().find((entry) => entry.execution_start !== null) ?? null);

  return (
    <div style={{ marginTop: 12 }}>
      <p className="muted small-text">
        <strong>Autorización de ejecución</strong>
        {current ? " · activa" : ""}
        {started ? " · con inicio atestiguado" : ""}
      </p>

      <p className="muted small-text" style={{ marginTop: 4 }}>
        <strong>Dependencias</strong>
      </p>
      <ul className="small-text" style={{ margin: 0, paddingLeft: 18 }}>
        <li>Definición vigente declarada: cumplida</li>
        <li>
          Condiciones declaradas ({definition.variant_count} de al menos {requiredVariants}):{" "}
          {hasVariants ? "cumplida" : "pendiente"}
        </li>
        <li>Contrato de medición: {hasContract ? "cumplida" : "pendiente"}</li>
        <li className="muted">
          No se exige al autorizar: contenido listo, distribución lista, seguimiento implementado o validado, ni
          identidad del público objetivo.
        </li>
      </ul>

      {!current ? (
        <p className="muted small-text" style={{ marginTop: 4 }}>
          No hay una autorización activa para este experimento.
        </p>
      ) : (
        <dl className="small-text" style={{ marginTop: 4 }}>
          <dt>Definición fijada</dt>
          <dd>{current.definition_version_id}</dd>
          <dt>Contrato de medición fijado</dt>
          <dd>{current.contract_version_id}</dd>
          <dt>Condiciones fijadas</dt>
          <dd>
            <ol style={{ margin: 0, paddingLeft: 18 }}>
              {current.variants.map((variant) => (
                <li key={variant.id}>
                  <strong>{variant.label}</strong>
                  <span style={{ whiteSpace: "pre-wrap", display: "block" }}>{variant.condition_description}</span>
                </li>
              ))}
            </ol>
          </dd>
          <dt>Unidad de asignación (declarada)</dt>
          <dd>{current.unit_of_assignment}</dd>
          <dt>Diseño de asignación (declarado)</dt>
          <dd style={{ whiteSpace: "pre-wrap" }}>{current.allocation_design}</dd>
          <dt>Señales del contrato</dt>
          <dd>
            {current.signal_count} declaradas, {current.tracking_required_signal_count} con seguimiento declarado
            (informativo; no indica que exista o esté validado)
          </dd>
          <dt>Declaración de medición fijada</dt>
          <dd data-testid="pinned-declaration-summary">
            {current.declaration_level === null
              ? "Sin declaración estructurada (heredado)."
              : `${current.declaration_level === "COMPARATIVE" ? "Comparativa" : "Descriptiva"} · semántica versión ${current.declaration_semantics_version} · ventana de medición ${current.measurement_window_days} días${
                  current.baseline_window_days !== null ? ` · ventana base ${current.baseline_window_days} días` : ""
                }. `}
            {current.declaration_level !== null &&
              "Al atestiguar el inicio queda congelada de forma permanente. Es una declaración: no valida ni prueba la evidencia."}
          </dd>
          <dt>Autorizada</dt>
          <dd>{formatDate(current.created_at)}</dd>
          {started && (
            <>
              <dt>Inicio atestiguado por una persona</dt>
              <dd>{formatDate(started.started_at)}</dd>
              <dt>Registrado en el sistema</dt>
              <dd>{formatDate(started.created_at)}</dd>
            </>
          )}
        </dl>
      )}

      {listError && (
        <p role="alert" className="settings-feedback">
          {listError}
        </p>
      )}

      <p className="muted small-text" style={{ marginTop: 4 }}>
        Autorizar no significa que la ejecución, la asignación, la exposición, la validación del seguimiento, la
        medición o un resultado hayan ocurrido. Solo registra que esta configuración exacta fue aprobada para comenzar
        más adelante. Mientras haya una autorización activa, el contrato de medición no admite revisiones. Una vez
        atestiguado el inicio, el contrato de medición y las condiciones declaradas quedan congelados de forma
        permanente, aunque la autorización se revoque.
      </p>
      {started && (
        <p className="muted small-text" style={{ marginTop: 4 }}>
          El inicio es una declaración de una persona; el sistema no verifica externamente que la ejecución haya
          comenzado y no implica asignación, entrega, exposición, evidencia ni resultado alguno.
        </p>
      )}

      {error && mode === "idle" && (
        <p role="alert" className="settings-feedback">
          {error}
        </p>
      )}

      {canAuthorize(role) && mode === "idle" && (
        <div className="settings-form-actions" style={{ marginTop: 8 }}>
          {started ? (
            <>
              <button
                type="button"
                className="button"
                disabled
                aria-describedby={`${idPrefix}-reauthorize-blocked`}
              >
                Volver a autorizar configuración
              </button>
              <span id={`${idPrefix}-reauthorize-blocked`} className="muted small-text">
                Esta autorización ya tiene un inicio atestiguado; revócala explícitamente antes de autorizar de nuevo.
              </span>
            </>
          ) : (
            <button type="button" className="button" onClick={openAuthorize}>
              {current ? "Volver a autorizar configuración" : "Autorizar configuración"}
            </button>
          )}
          {current && !started && (
            <button type="button" className="button" onClick={openStart}>
              Registrar inicio de ejecución
            </button>
          )}
          {current && (
            <button type="button" className="button" onClick={openRevoke}>
              Revocar autorización
            </button>
          )}
        </div>
      )}

      {mode === "authorize" && (
        <div className="panel" style={{ marginTop: 8 }}>
          <p className="muted small-text">
            Se fijarán la versión vigente de la definición, todas las condiciones declaradas y la versión vigente del
            contrato de medición. Una nueva autorización reemplaza la activa solo si esta aún no tiene un inicio
            atestiguado.
          </p>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-unit`}>Unidad de asignación</label>
            <input
              id={`${idPrefix}-unit`}
              type="text"
              value={unit}
              disabled={pending}
              onChange={(event) => setUnit(event.target.value)}
            />
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-design`}>Diseño de asignación</label>
            <textarea
              id={`${idPrefix}-design`}
              value={design}
              disabled={pending}
              onChange={(event) => setDesign(event.target.value)}
            />
          </div>
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button type="button" className="button primary" disabled={pending} onClick={submitAuthorize}>
              Confirmar autorización
            </button>
            <button type="button" className="button" disabled={pending} onClick={close}>
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

      {mode === "start" && (
        <div className="panel" style={{ marginTop: 8 }}>
          <p className="muted small-text">
            Vas a declarar, como persona, que la ejecución de esta autorización comenzó. Es una declaración: el sistema
            no la verifica externamente. No se puede corregir. Congela de forma permanente el contrato de medición y la
            declaración de nuevas condiciones de este experimento, incluso si luego revocas la autorización; para
            corregir algo tendrás que crear un nuevo experimento.
          </p>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-started-at`}>Inicio atestiguado (fecha y hora)</label>
            <input
              id={`${idPrefix}-started-at`}
              type="datetime-local"
              step="1"
              value={startedAtInput}
              disabled={pending}
              onChange={(event) => setStartedAtInput(event.target.value)}
            />
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-start-confirm`}>
              <input
                id={`${idPrefix}-start-confirm`}
                type="checkbox"
                checked={startConfirmed}
                disabled={pending}
                onChange={(event) => setStartConfirmed(event.target.checked)}
              />{" "}
              Entiendo que es una declaración mía, que no se puede corregir y que congela el contrato y las condiciones.
            </label>
          </div>
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button type="button" className="button primary" disabled={pending} onClick={submitStart}>
              Confirmar inicio declarado
            </button>
            <button type="button" className="button" disabled={pending} onClick={close}>
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

      {mode === "revoke" && (
        <div className="panel" style={{ marginTop: 8 }}>
          <p className="muted small-text">
            La revocación retira la autoridad para comenzar o continuar la ejecución futura. No significa que una
            asignación, exposición o evidencia pasada no haya ocurrido. No se puede deshacer. Si la autorización tiene un
            inicio atestiguado, este se conserva y el congelamiento del contrato y de las condiciones permanece.
          </p>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-reason`}>Motivo de la revocación</label>
            <textarea
              id={`${idPrefix}-reason`}
              value={reason}
              disabled={pending}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button type="button" className="button primary" disabled={pending} onClick={submitRevoke}>
              Confirmar revocación
            </button>
            <button type="button" className="button" disabled={pending} onClick={close}>
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

      {past.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <p className="muted small-text">
            <strong>Autorizaciones anteriores</strong>
          </p>
          <ol className="small-text" style={{ margin: 0, paddingLeft: 18 }}>
            {past.map((entry) => (
              <li key={entry.id}>
                {entry.id} · {formatDate(entry.created_at)} ·{" "}
                {entry.active
                  ? "activa"
                  : entry.superseded_by
                    ? `reemplazada por ${entry.superseded_by}`
                    : "revocada"}
                {entry.revoked_reason ? ` — ${entry.revoked_reason}` : ""}
                {entry.execution_start
                  ? ` · inicio atestiguado ${formatDate(entry.execution_start.started_at)} (registrado ${formatDate(entry.execution_start.created_at)})`
                  : ""}
              </li>
            ))}
          </ol>
        </div>
      )}

      {claimsTarget?.execution_start && (
        <EvidenceClaimsSection
          key={claimsTarget.execution_start.id}
          campaignId={campaignId}
          experimentId={experiment.id}
          startId={claimsTarget.execution_start.id}
          role={role}
        />
      )}
    </div>
  );
}
