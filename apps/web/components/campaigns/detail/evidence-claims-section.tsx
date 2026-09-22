"use client";

// Experiment Evidence Binding (frozen Design Freeze). Minimum operational UI in the Start section: the claims
// of ONE started execution attempt (disposed claims stay visible), a create form, a dispose action with a
// reason, and a PERSISTENT provenance-only warning.
//
// EVIDENCE CLAIM != ELIGIBILITY != VALIDATION != CURRENTNESS != SUFFICIENCY != CORRECTNESS != TRACKING
// VALIDITY != ASSIGNMENT != EXPOSURE != VARIANT ATTRIBUTION != MEASUREMENT != RESULT != WINNER != ATTRIBUTION
// != CAUSALITY. A claim only records that a member associated ONE metric datum (an entry + a metric name)
// with ONE required signal under this started attempt, at EXPERIMENT level. The copy below never says a datum
// is eligible, accepted, verified or valid; there is no accept/verify/qualify/Variant/assignment/exposure/
// result control and no free-text note.
//
// The datum picker uses the existing AGGREGATE metrics listing only (EEB-DF-OBS-8): distribution-owned
// entries are claimable through the API but are not discoverable here — though they are rendered correctly,
// with their distribution context, whenever they appear in the list.
//
// Idempotency (create): `client_request_id` is kept across retries of the SAME material (signal + entry +
// metric) so a lost response replays as a 200, and rotated on success or IDEMPOTENCY_KEY_CONFLICT. Dispose
// carries no key: a repeated dispose is a deterministic 409 that simply refreshes the view.

import { useEffect, useRef, useState } from "react";
import {
  createEvidenceClaim,
  disposeEvidenceClaim,
  getMeasurementContract,
  listEvidenceClaims,
} from "@/lib/api/strategy";
import { getMetrics } from "@/lib/api/measurement";
import { ApiError } from "@/lib/api/client";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { MetricEntryPublic } from "@/types/measurement";
import type { EvidenceClaimPublic, RequiredSignalPublic } from "@/types/strategy";

const REASON_MAX = 1000;

// The same MEMBER+ tier as authorize/revoke/start (frozen §AC).
function canClaim(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN" || role === "MEMBER";
}

function describeClaimError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "EVIDENCE_CLAIM_ALREADY_ACTIVE":
        return "Ya existe una afirmación activa para este dato y esta señal bajo este inicio. Se actualizó la vista.";
      case "EVIDENCE_CLAIM_ALREADY_DISPOSED":
        return "Esta afirmación ya fue descartada. Se actualizó la vista.";
      case "EVIDENCE_CLAIM_METRIC_NOT_IN_ENTRY":
        return "La métrica elegida no existe en esa entrada de métricas.";
      case "EVIDENCE_CLAIM_SIGNAL_NOT_IN_PINNED_CONTRACT":
        return "Esa señal no pertenece al contrato de medición fijado por este inicio.";
      case "EVIDENCE_CLAIM_METRIC_NOT_BOUND":
        return "La métrica elegida no es la que esta señal declara en el contrato de medición (se compara de forma exacta, distinguiendo mayúsculas).";
      case "EVIDENCE_CLAIM_CHANNEL_NOT_BOUND":
        return "El canal de esa entrada no es el canal exacto que esta señal declara en el contrato de medición.";
      case "IDEMPOTENCY_KEY_CONFLICT":
        return "Esta solicitud no coincide con un envío anterior. Revisa los datos e inténtalo de nuevo.";
    }
  }
  return describeCampaignError(error);
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString("es");
}

function metricEntryLabel(entry: MetricEntryPublic): string {
  return `${entry.id} · ${entry.channel} · ${entry.period_start} – ${entry.period_end} · ${entry.source}${
    entry.is_current ? "" : " · existe una entrada posterior en la misma agrupación"
  }`;
}

export function EvidenceClaimsSection({
  campaignId,
  experimentId,
  startId,
  role,
}: {
  campaignId: string;
  experimentId: string;
  startId: string;
  role: string | null;
}) {
  const [claims, setClaims] = useState<EvidenceClaimPublic[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [listError, setListError] = useState("");
  const [reloadToken, setReloadToken] = useState(0);

  const [mode, setMode] = useState<"idle" | "create" | "dispose">("idle");
  const [signals, setSignals] = useState<RequiredSignalPublic[]>([]);
  const [entries, setEntries] = useState<MetricEntryPublic[]>([]);
  const [optionsLoading, setOptionsLoading] = useState(false);
  const [signalId, setSignalId] = useState("");
  const [entryId, setEntryId] = useState("");
  const [metricName, setMetricName] = useState("");
  const [disposingId, setDisposingId] = useState("");
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const attemptRef = useRef<{ key: string; material: string } | null>(null);
  const idPrefix = `evidence-claims-${startId}`;

  useEffect(() => {
    let cancelled = false;
    listEvidenceClaims(campaignId, experimentId, startId)
      .then((response) => {
        if (!cancelled) {
          setClaims(response.claims);
          setListError("");
          setLoaded(true);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setListError(describeCampaignError(caught));
          setLoaded(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, experimentId, startId, reloadToken]);

  function close() {
    setMode("idle");
    setSignalId("");
    setEntryId("");
    setMetricName("");
    setDisposingId("");
    setReason("");
  }

  async function openCreate() {
    setError("");
    setSignalId("");
    setEntryId("");
    setMetricName("");
    setMode("create");
    setOptionsLoading(true);
    try {
      // After a Start the Contract lineage is permanently frozen, so the current tip IS the pinned contract.
      const [contract, metrics] = await Promise.all([getMeasurementContract(campaignId, experimentId), getMetrics(campaignId)]);
      setSignals(contract?.signals ?? []);
      setEntries(metrics.items);
    } catch (caught) {
      setError(describeCampaignError(caught));
    } finally {
      setOptionsLoading(false);
    }
  }

  function openDispose(claimId: string) {
    setDisposingId(claimId);
    setReason("");
    setError("");
    setMode("dispose");
  }

  async function submitCreate() {
    if (pending) return;
    if (!signalId) {
      setError("Elige la señal requerida.");
      return;
    }
    if (!entryId) {
      setError("Elige la entrada de métricas.");
      return;
    }
    if (!metricName) {
      setError("Elige la métrica.");
      return;
    }
    const material = `${signalId}|${entryId}|${metricName}`;
    if (attemptRef.current === null || attemptRef.current.material !== material) {
      attemptRef.current = { key: crypto.randomUUID(), material };
    }
    setPending(true);
    setError("");
    try {
      await createEvidenceClaim(campaignId, experimentId, startId, {
        client_request_id: attemptRef.current.key,
        required_signal_id: signalId,
        metric_entry_id: entryId,
        metric_name: metricName,
      });
      attemptRef.current = null;
      close();
      setReloadToken((token) => token + 1);
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : null;
      if (code === "IDEMPOTENCY_KEY_CONFLICT") attemptRef.current = null;
      setError(describeClaimError(caught));
      if (code === "EVIDENCE_CLAIM_ALREADY_ACTIVE") {
        // The state moved on: refetch, never retry blindly.
        attemptRef.current = null;
        close();
        setReloadToken((token) => token + 1);
      }
    } finally {
      setPending(false);
    }
  }

  async function submitDispose() {
    if (pending || !disposingId) return;
    const trimmed = reason.trim();
    if (trimmed.length === 0) {
      setError("Debes indicar el motivo para descartar la afirmación.");
      return;
    }
    if (trimmed.length > REASON_MAX) {
      setError(`El motivo no puede superar ${REASON_MAX} caracteres.`);
      return;
    }
    setPending(true);
    setError("");
    try {
      await disposeEvidenceClaim(campaignId, experimentId, startId, disposingId, { reason: trimmed });
      close();
      setReloadToken((token) => token + 1);
    } catch (caught) {
      setError(describeClaimError(caught));
      if (caught instanceof ApiError && caught.code === "EVIDENCE_CLAIM_ALREADY_DISPOSED") {
        close();
        setReloadToken((token) => token + 1);
      }
    } finally {
      setPending(false);
    }
  }

  const selectedEntry = entries.find((entry) => entry.id === entryId) ?? null;
  const metricNames = selectedEntry ? Object.keys(selectedEntry.values) : [];
  // Pre-Execution Measurement Declaration: a signal of a STRUCTURED contract declares which metric/channel it is read
  // from. Only a suggestion — structural compatibility, never eligibility or validation.
  const selectedSignal = signals.find((signal) => signal.id === signalId) ?? null;
  const declaredMetric = selectedSignal?.bound_metric_name ?? null;

  return (
    <div style={{ marginTop: 16 }} data-testid="evidence-claims-section">
      <p className="muted small-text">
        <strong>Afirmaciones de evidencia</strong> · nivel de experimento · inicio {startId}
      </p>
      <p className="muted small-text" role="note" style={{ marginTop: 4 }}>
        <strong>SOLO AFIRMACIÓN DE PROCEDENCIA — NO ES ELEGIBILIDAD NI VALIDACIÓN.</strong> Una afirmación registra que
        una persona asoció un dato de métrica con una señal requerida bajo este inicio atestiguado. No significa que el
        dato sea elegible, vigente, suficiente ni correcto, que provenga de la ejecución o del periodo posterior al
        inicio, ni que el seguimiento sea válido. Tampoco implica asignación, exposición, atribución a una condición,
        medición, resultado, ganador, atribución comercial ni causalidad. «Afirmada por» es quien asoció el dato con la
        señal, no necesariamente quien reportó originalmente la métrica.
      </p>

      {listError && (
        <p role="alert" className="settings-feedback">
          {listError}
        </p>
      )}

      {loaded && !listError && claims.length === 0 && (
        <p className="muted small-text" style={{ marginTop: 4 }}>
          Aún no hay afirmaciones de evidencia para este inicio.
        </p>
      )}

      {claims.length > 0 && (
        <ol className="small-text" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
          {claims.map((claim) => (
            <li key={claim.id} style={{ marginBottom: 8 }}>
              <strong>{claim.id}</strong> · {claim.is_disposed ? "descartada" : "activa"}
              <dl style={{ margin: "2px 0 0" }}>
                <dt>Señal requerida</dt>
                <dd>
                  {claim.required_signal ? (
                    <>
                      {claim.required_signal.name} ({claim.required_signal.id}) · seguimiento declarado:{" "}
                      {claim.required_signal.tracking_required ? "sí" : "no"} (informativo; no indica que exista o esté
                      validado)
                    </>
                  ) : (
                    "—"
                  )}
                </dd>
                <dt>Dato afirmado</dt>
                <dd>
                  {claim.datum.metric_name} = {claim.datum.value === null ? "—" : String(claim.datum.value)} · entrada{" "}
                  {claim.datum.metric_entry_id}
                </dd>
                <dt>Periodo de la entrada</dt>
                <dd>
                  {claim.datum.period_start ?? "—"} – {claim.datum.period_end ?? "—"} · canal{" "}
                  {claim.datum.channel ?? "—"} · fuente {claim.datum.source ?? "—"}
                </dd>
                <dt>Inicio atestiguado</dt>
                <dd>{formatDate(claim.start.started_at)} (se muestra junto al periodo; no se compara)</dd>
                <dt>Afirmada por</dt>
                <dd>
                  {claim.claimed_by ?? "—"} · {formatDate(claim.created_at)}
                </dd>
                {claim.authorization.revoked_at && (
                  <>
                    <dt>Autorización</dt>
                    <dd>
                      {claim.authorization.id} revocada el {formatDate(claim.authorization.revoked_at)}
                      {claim.authorization.revoked_reason ? ` — ${claim.authorization.revoked_reason}` : ""}
                    </dd>
                  </>
                )}
                {claim.later_correction_exists && (
                  <>
                    <dt>Observación</dt>
                    <dd>
                      Al consultar, existe una corrección posterior de este dato. Esta afirmación sigue apuntando a la
                      entrada original; afirmar el dato corregido requiere otra afirmación. Es una observación de
                      lectura, no un estado.
                    </dd>
                  </>
                )}
                {claim.distribution && (
                  <>
                    <dt>Evidencia de distribución</dt>
                    <dd>
                      {claim.distribution.evidence_id}
                      {claim.distribution.distribution_id ? ` · distribución ${claim.distribution.distribution_id}` : ""}
                      {claim.distribution.source_reference ? ` · referencia ${claim.distribution.source_reference}` : ""}
                      {claim.distribution.supersedes_evidence_id
                        ? ` · corrige ${claim.distribution.supersedes_evidence_id}`
                        : ""}
                      {claim.distribution.superseded_by_evidence_id
                        ? ` · corregida por ${claim.distribution.superseded_by_evidence_id}`
                        : ""}
                      {claim.excluded_from_aggregate_and_analysis
                        ? " · excluida del listado agregado y del análisis genérico"
                        : ""}
                    </dd>
                  </>
                )}
                {claim.is_disposed && (
                  <>
                    <dt>Descartada</dt>
                    <dd>
                      {claim.disposed_at ? formatDate(claim.disposed_at) : "—"} por {claim.disposed_by ?? "—"}
                      {claim.disposal_reason ? ` — ${claim.disposal_reason}` : ""}. Descartar solo indica que el
                      espacio de trabajo ya no quiere que se considere; no significa que la afirmación no ocurriera.
                    </dd>
                  </>
                )}
              </dl>
              {canClaim(role) && !claim.is_disposed && mode === "idle" && (
                <button type="button" className="button" onClick={() => openDispose(claim.id)}>
                  Descartar afirmación {claim.id}
                </button>
              )}
            </li>
          ))}
        </ol>
      )}

      {error && mode === "idle" && (
        <p role="alert" className="settings-feedback">
          {error}
        </p>
      )}

      {canClaim(role) && mode === "idle" && (
        <div className="settings-form-actions" style={{ marginTop: 8 }}>
          <button type="button" className="button" onClick={openCreate}>
            Registrar afirmación de evidencia
          </button>
        </div>
      )}

      {mode === "create" && (
        <div className="panel" style={{ marginTop: 8 }}>
          <p className="muted small-text">
            Vas a afirmar, como persona, que un dato de métrica está asociado con una señal requerida bajo este inicio.
            Es solo una afirmación de procedencia a nivel de experimento: no la valida el sistema.
          </p>
          {optionsLoading && <p className="muted small-text">Cargando señales y entradas de métricas…</p>}
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-signal`}>Señal requerida</label>
            <select
              id={`${idPrefix}-signal`}
              value={signalId}
              disabled={pending || optionsLoading}
              onChange={(event) => setSignalId(event.target.value)}
            >
              <option value="">Elige una señal</option>
              {signals.map((signal) => (
                <option key={signal.id} value={signal.id}>
                  {signal.name} ({signal.id})
                </option>
              ))}
            </select>
            {selectedSignal && declaredMetric !== null && (
              <p className="muted small-text" data-testid="declared-binding-hint">
                Esta señal declara la métrica «{declaredMetric}» en{" "}
                {selectedSignal.channel_binding === "EXACT"
                  ? `el canal exacto «${selectedSignal.bound_channel}»`
                  : "cualquier canal"}
                . Solo se aceptan afirmaciones estructuralmente compatibles con esa declaración (comparación exacta,
                distinguiendo mayúsculas); eso no significa que el dato sea elegible, válido ni suficiente.
              </p>
            )}
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-entry`}>Entrada de métricas</label>
            <select
              id={`${idPrefix}-entry`}
              value={entryId}
              disabled={pending || optionsLoading}
              onChange={(event) => {
                setEntryId(event.target.value);
                setMetricName("");
              }}
            >
              <option value="">Elige una entrada</option>
              {entries.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {metricEntryLabel(entry)}
                </option>
              ))}
            </select>
          </div>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-metric`}>Métrica</label>
            <select
              id={`${idPrefix}-metric`}
              value={metricName}
              disabled={pending || optionsLoading || metricNames.length === 0}
              onChange={(event) => setMetricName(event.target.value)}
            >
              <option value="">Elige una métrica</option>
              {metricNames.map((name) => (
                <option key={name} value={name}>
                  {name}
                  {declaredMetric !== null && name === declaredMetric ? " (declarada)" : ""}
                </option>
              ))}
            </select>
          </div>
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button type="button" className="button primary" disabled={pending || optionsLoading} onClick={submitCreate}>
              Confirmar afirmación
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

      {mode === "dispose" && (
        <div className="panel" style={{ marginTop: 8 }}>
          <p className="muted small-text">
            Descartar es definitivo y no se puede deshacer. Solo indica que el espacio de trabajo ya no quiere que esta
            afirmación se considere en el futuro; no significa que la afirmación no ocurriera ni que el dato, el
            experimento o la afirmación fueran falsos o inválidos. La afirmación descartada sigue visible.
          </p>
          <div className="settings-field">
            <label htmlFor={`${idPrefix}-reason`}>Motivo para descartar</label>
            <textarea
              id={`${idPrefix}-reason`}
              value={reason}
              disabled={pending}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button type="button" className="button primary" disabled={pending} onClick={submitDispose}>
              Confirmar descarte
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
    </div>
  );
}
