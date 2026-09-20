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

import { useEffect, useRef, useState } from "react";
import {
  authorizeExecution,
  getExecutionAuthorization,
  getExecutionAuthorizationHistory,
  revokeExecutionAuthorization,
} from "@/lib/api/strategy";
import { ApiError } from "@/lib/api/client";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type {
  ExecutionAuthorizationPublic,
  ExperimentDefinitionPublic,
  ExperimentPublic,
} from "@/types/strategy";

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
      case "IDEMPOTENCY_KEY_CONFLICT":
        return "Esta solicitud no coincide con un envío anterior o la configuración vigente cambió. Revisa los datos e inténtalo de nuevo.";
    }
  }
  return describeCampaignError(error);
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString("es");
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

  const [mode, setMode] = useState<"idle" | "authorize" | "revoke">("idle");
  const [unit, setUnit] = useState("");
  const [design, setDesign] = useState("");
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  // Lazy state initializer: the initial key is generated exactly once.
  const [initialClientRequestId] = useState(() => crypto.randomUUID());
  const clientRequestIdRef = useRef(initialClientRequestId);
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

  function close() {
    setMode("idle");
    setUnit("");
    setDesign("");
    setReason("");
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

  const past = history.filter((entry) => entry.id !== current?.id);

  return (
    <div style={{ marginTop: 12 }}>
      <p className="muted small-text">
        <strong>Autorización de ejecución</strong>
        {current ? " · activa" : ""}
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
          <dt>Autorizada</dt>
          <dd>{formatDate(current.created_at)}</dd>
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
        más adelante. Mientras haya una autorización activa, el contrato de medición no admite revisiones.
      </p>

      {error && mode === "idle" && (
        <p role="alert" className="settings-feedback">
          {error}
        </p>
      )}

      {canAuthorize(role) && mode === "idle" && (
        <div className="settings-form-actions" style={{ marginTop: 8 }}>
          <button type="button" className="button" onClick={openAuthorize}>
            {current ? "Volver a autorizar configuración" : "Autorizar configuración"}
          </button>
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
            contrato de medición. Una nueva autorización reemplaza la activa.
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

      {mode === "revoke" && (
        <div className="panel" style={{ marginTop: 8 }}>
          <p className="muted small-text">
            La revocación retira la autoridad para comenzar o continuar la ejecución futura. No significa que una
            asignación, exposición o evidencia pasada no haya ocurrido. No se puede deshacer.
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
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
