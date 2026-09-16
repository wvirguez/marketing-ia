"use client";

// MVP-19B: Distribution-linked Measurement Evidence UI. Renders only when
// the ContentPiece/Distribution has reached DISTRIBUTED (the parent,
// ContentDetailView, decides that and passes it down as `distributed`).
//
// CORE SEMANTIC (frozen, MVP-19A §4): this section may only ever say "the
// user reported these metrics for this Distribution," never that the
// Distribution generated/caused them. No attribution wording, no
// verification badge, no causal language anywhere in this file.
//
// Every mutation goes through the real, authenticated, CSRF-protected
// backend routes (apps/api/app/measurement/router.py) — no optimistic
// state: every write reloads the evidence list fresh from the server
// afterward, and a failed write leaves the last confirmed list untouched.
// Single-flight: at most one create/correction may be in flight at a time
// from this section.

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Icon } from "@/components/ui/icon";
import {
  getDistributionEvidenceSummary,
  listDistributionEvidence,
  recordDistributionEvidence,
  recordDistributionEvidenceCorrection,
} from "@/lib/api/content";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { formatCampaignDate } from "@/lib/campaigns/status";
import type { DistributionEvidencePublic, DistributionEvidenceSummaryPublic } from "@/types/content";

type ListState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; items: DistributionEvidencePublic[]; total: number };

// MVP-21: purely server-computed — this component never recomputes
// report_count/latest/earliest from the raw evidence history above. Fetched
// and rendered independently of that history's own loading/error state
// (MVP-21B §33: a summary-fetch failure must never hide the raw history or
// block the rest of Content Detail).
type SummaryState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; summary: DistributionEvidenceSummaryPublic };

const SUMMARY_TITLE = "Resumen de evidencia reportada";
const SUMMARY_SUBTITLE = "Registro manual; sin atribución causal.";
const SUMMARY_EMPTY_COPY = "Aún no hay evidencia reportada para resumir.";

function EvidenceSummaryPanel({
  campaignId,
  contentId,
  distributed,
  refreshToken,
}: {
  campaignId: string;
  contentId: string;
  distributed: boolean;
  refreshToken: number;
}) {
  const [state, setState] = useState<SummaryState>({ status: "loading" });

  function reload() {
    setState({ status: "loading" });
    getDistributionEvidenceSummary(campaignId, contentId)
      .then((summary) => setState({ status: "ready", summary }))
      .catch((error) => setState({ status: "error", message: describeCampaignError(error) }));
  }

  useEffect(() => {
    if (!distributed) return;
    let cancelled = false;
    getDistributionEvidenceSummary(campaignId, contentId)
      .then((summary) => {
        if (!cancelled) setState({ status: "ready", summary });
      })
      .catch((error) => {
        if (!cancelled) setState({ status: "error", message: describeCampaignError(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, contentId, distributed, refreshToken]);

  if (!distributed) return null;

  return (
    <div className="panel" style={{ marginTop: 12 }}>
      <div className="section-heading">
        <h4>{SUMMARY_TITLE}</h4>
      </div>
      <p className="muted small-text">{SUMMARY_SUBTITLE}</p>

      {state.status === "loading" && (
        <p className="muted small-text" role="status">
          Cargando resumen…
        </p>
      )}

      {state.status === "error" && (
        <p className="small-text" role="alert">
          {state.message}{" "}
          <button type="button" className="auth-text-button" onClick={reload}>
            Reintentar
          </button>
        </p>
      )}

      {state.status === "ready" &&
        (state.summary.metrics.length === 0 ? (
          <p className="muted small-text">{SUMMARY_EMPTY_COPY}</p>
        ) : (
          <dl style={{ margin: "10px 0 0" }}>
            {state.summary.metrics.map((metric) => (
              <div key={metric.metric_name} style={{ marginTop: 8 }}>
                <dt>{metric.metric_name}</dt>
                <dd className="muted small-text" style={{ margin: 0 }}>
                  Último valor reportado {metric.latest_value} ({metric.latest_period_start} –{" "}
                  {metric.latest_period_end}), {metric.report_count} reporte(s).
                </dd>
              </div>
            ))}
          </dl>
        ))}
    </div>
  );
}

type MetricRow = { id: number; name: string; value: string };

const SECTION_TITLE = "Métricas reportadas para esta distribución";
const SECTION_SUBTITLE = "Registro manual; sin atribución causal.";
const EMPTY_COPY = "Aún no hay métricas reportadas para esta distribución.";
const CURRENT_COPY = "Registro actual";
const SUPERSEDED_COPY = "Reemplazado por una corrección";

function isValidDecimal(value: string): boolean {
  return /^-?\d+(\.\d+)?$/.test(value.trim());
}

function newRow(id: number): MetricRow {
  return { id, name: "", value: "" };
}

function EvidenceForm({
  campaignId,
  contentId,
  target,
  onCancel,
  onSaved,
}: {
  campaignId: string;
  contentId: string;
  // Present only for a correction: the current evidence row being replaced.
  target?: DistributionEvidencePublic;
  onCancel?: () => void;
  onSaved: () => void;
}) {
  const isCorrection = target !== undefined;
  const [periodStart, setPeriodStart] = useState(target?.period_start ?? "");
  const [periodEnd, setPeriodEnd] = useState(target?.period_end ?? "");
  const [sourceReference, setSourceReference] = useState(target?.source_reference ?? "");
  const [correctionReason, setCorrectionReason] = useState("");
  const [rows, setRows] = useState<MetricRow[]>(() => {
    const entries = target ? Object.entries(target.values) : [];
    return entries.length > 0
      ? entries.map(([name, value], index) => ({ id: index, name, value: String(value) }))
      : [newRow(0)];
  });
  const nextRowId = useRef(rows.length);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [submitError, setSubmitError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // One stable id per logical submission, reused across retries of the
  // same unresolved attempt, regenerated only after a confirmed success —
  // mirrors MetricsPanel's own idempotency-key discipline.
  const clientRequestIdRef = useRef(crypto.randomUUID());

  function addRow() {
    setRows((prev) => [...prev, newRow(nextRowId.current++)]);
  }

  function removeRow(id: number) {
    setRows((prev) => (prev.length <= 1 ? prev : prev.filter((row) => row.id !== id)));
  }

  function updateRow(id: number, patch: Partial<Pick<MetricRow, "name" | "value">>) {
    setRows((prev) => prev.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  }

  function validate(): Record<string, string> {
    const errors: Record<string, string> = {};
    if (!periodStart) errors.periodStart = "Ingresa la fecha de inicio.";
    if (!periodEnd) errors.periodEnd = "Ingresa la fecha de fin.";
    if (periodStart && periodEnd && periodEnd < periodStart) {
      errors.periodEnd = "La fecha de fin debe ser igual o posterior a la de inicio.";
    }
    if (isCorrection && !correctionReason.trim()) {
      errors.correctionReason = "Explica por qué corriges este registro.";
    }
    const seenNames = new Set<string>();
    for (const row of rows) {
      const name = row.name.trim();
      if (!name) {
        errors[`row-${row.id}-name`] = "Ingresa un nombre de métrica.";
      } else {
        const normalized = name.toLowerCase();
        if (seenNames.has(normalized)) errors[`row-${row.id}-name`] = "Ya agregaste una métrica con este nombre.";
        seenNames.add(normalized);
      }
      if (row.value.trim() === "") {
        errors[`row-${row.id}-value`] = "Ingresa un valor.";
      } else if (!isValidDecimal(row.value)) {
        errors[`row-${row.id}-value`] = "Ingresa un número válido (por ejemplo: 120 o 3.5).";
      }
    }
    return errors;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setSubmitError("");
    const errors = validate();
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    const values: Record<string, string> = {};
    for (const row of rows) values[row.name.trim()] = row.value.trim();

    setSubmitting(true);
    try {
      if (target) {
        await recordDistributionEvidenceCorrection(campaignId, contentId, target.id, {
          period_start: periodStart,
          period_end: periodEnd,
          values,
          client_request_id: clientRequestIdRef.current,
          source_reference: sourceReference.trim() || null,
          correction_reason: correctionReason.trim(),
        });
      } else {
        await recordDistributionEvidence(campaignId, contentId, {
          period_start: periodStart,
          period_end: periodEnd,
          values,
          client_request_id: clientRequestIdRef.current,
          source_reference: sourceReference.trim() || null,
        });
      }
      // Confirmed success: safe to start a fresh idempotency key.
      clientRequestIdRef.current = crypto.randomUUID();
      onSaved();
    } catch (error) {
      // Ambiguous failure: form values and the idempotency key are both
      // retained untouched, so a retry of this same submission reuses it.
      setSubmitError(describeCampaignError(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="panel" onSubmit={handleSubmit} noValidate style={{ marginTop: 12 }}>
      <div className="section-heading">
        <h4>{isCorrection ? "Corregir métricas reportadas" : "Agregar métricas reportadas"}</h4>
      </div>

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <div>
          <label htmlFor="evidence-period-start">Inicio del período</label>
          <br />
          <input id="evidence-period-start" type="date" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} />
          {fieldErrors.periodStart && <p className="small-text" role="alert">{fieldErrors.periodStart}</p>}
        </div>
        <div>
          <label htmlFor="evidence-period-end">Fin del período</label>
          <br />
          <input id="evidence-period-end" type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} />
          {fieldErrors.periodEnd && <p className="small-text" role="alert">{fieldErrors.periodEnd}</p>}
        </div>
      </div>

      <div className="section-heading" style={{ marginTop: 16 }}>
        <h4>Métricas</h4>
      </div>
      {rows.map((row, index) => (
        <div key={row.id} style={{ display: "flex", gap: 12, alignItems: "flex-start", marginTop: 10, flexWrap: "wrap" }}>
          <div>
            <label htmlFor={`evidence-name-${row.id}`}>Nombre de la métrica</label>
            <br />
            <input
              id={`evidence-name-${row.id}`}
              type="text"
              placeholder="Ej. alcance"
              value={row.name}
              onChange={(event) => updateRow(row.id, { name: event.target.value })}
            />
            {fieldErrors[`row-${row.id}-name`] && <p className="small-text" role="alert">{fieldErrors[`row-${row.id}-name`]}</p>}
          </div>
          <div>
            <label htmlFor={`evidence-value-${row.id}`}>Valor</label>
            <br />
            <input
              id={`evidence-value-${row.id}`}
              type="text"
              inputMode="decimal"
              placeholder="Ej. 500"
              value={row.value}
              onChange={(event) => updateRow(row.id, { value: event.target.value })}
            />
            {fieldErrors[`row-${row.id}-value`] && <p className="small-text" role="alert">{fieldErrors[`row-${row.id}-value`]}</p>}
          </div>
          <button
            type="button"
            className="auth-text-button"
            onClick={() => removeRow(row.id)}
            disabled={rows.length <= 1}
            aria-label={`Eliminar métrica ${index + 1}`}
            style={{ marginTop: 22 }}
          >
            Eliminar
          </button>
        </div>
      ))}
      <div style={{ marginTop: 12 }}>
        <button type="button" className="auth-text-button" onClick={addRow}>
          Agregar métrica
        </button>
      </div>

      <div style={{ marginTop: 16 }}>
        <label htmlFor="evidence-source-reference">Referencia de origen (opcional)</label>
        <br />
        <input
          id="evidence-source-reference"
          type="text"
          maxLength={2048}
          placeholder="Ej. captura del panel de la plataforma"
          value={sourceReference}
          onChange={(event) => setSourceReference(event.target.value)}
        />
      </div>

      {isCorrection && (
        <div style={{ marginTop: 16 }}>
          <label htmlFor="evidence-correction-reason">Motivo de la corrección</label>
          <br />
          <input
            id="evidence-correction-reason"
            type="text"
            maxLength={500}
            value={correctionReason}
            onChange={(event) => setCorrectionReason(event.target.value)}
          />
          {fieldErrors.correctionReason && <p className="small-text" role="alert">{fieldErrors.correctionReason}</p>}
        </div>
      )}

      {submitError && (
        <p className="small-text" role="alert" style={{ marginTop: 16 }}>
          {submitError}
        </p>
      )}

      <div style={{ marginTop: 16, display: "flex", gap: 10 }}>
        <button type="submit" className="button primary" disabled={submitting}>
          {submitting ? "Guardando…" : isCorrection ? "Guardar corrección" : "Agregar métricas reportadas"}
        </button>
        {onCancel && (
          <button type="button" className="auth-text-button" onClick={onCancel} disabled={submitting}>
            Cancelar
          </button>
        )}
      </div>
    </form>
  );
}

function EvidenceCard({
  evidence,
  onCorrect,
  correcting,
}: {
  evidence: DistributionEvidencePublic;
  onCorrect: () => void;
  correcting: boolean;
}) {
  return (
    <article className="panel deliverable-card">
      <span className="deliverable-icon">
        <Icon name="chart" size={19} />
      </span>
      <div>
        <h3>
          {evidence.period_start} – {evidence.period_end}
        </h3>
        <dl style={{ margin: "10px 0 0" }}>
          {Object.entries(evidence.values).map(([name, value]) => (
            <div key={name}>
              <dt className="muted small-text">{name}</dt>
              <dd style={{ margin: 0 }}>{value}</dd>
            </div>
          ))}
        </dl>
        {evidence.source_reference && <p className="muted small-text" style={{ marginTop: 10 }}>Origen: {evidence.source_reference}</p>}
        <p className="muted small-text" style={{ marginTop: 10 }}>Reportado el {formatCampaignDate(evidence.reported_at)}</p>
        <p className="muted small-text">{evidence.is_current ? CURRENT_COPY : SUPERSEDED_COPY}</p>
        {evidence.correction_reason && <p className="muted small-text">Motivo de corrección: {evidence.correction_reason}</p>}
        {evidence.is_current && !correcting && (
          <button type="button" className="auth-text-button" onClick={onCorrect} style={{ marginTop: 8 }}>
            Corregir
          </button>
        )}
      </div>
    </article>
  );
}

export function ContentDistributionEvidenceSection({
  campaignId,
  contentId,
  distributed,
}: {
  campaignId: string;
  contentId: string;
  distributed: boolean;
}) {
  const [state, setState] = useState<ListState>({ status: "loading" });
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [correctingId, setCorrectingId] = useState<string | null>(null);
  const [summaryRefreshToken, setSummaryRefreshToken] = useState(0);

  function reload() {
    setState({ status: "loading" });
    listDistributionEvidence(campaignId, contentId)
      .then((response) => setState({ status: "ready", items: response.items, total: response.total }))
      .catch((error) => setState({ status: "error", message: describeCampaignError(error) }));
    setSummaryRefreshToken((token) => token + 1);
  }

  useEffect(() => {
    if (!distributed) return;
    let cancelled = false;
    listDistributionEvidence(campaignId, contentId)
      .then((response) => {
        if (!cancelled) setState({ status: "ready", items: response.items, total: response.total });
      })
      .catch((error) => {
        if (!cancelled) setState({ status: "error", message: describeCampaignError(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, contentId, distributed]);

  if (!distributed) return null;

  return (
    <section className="panel" style={{ marginTop: 16 }}>
      <div className="section-heading">
        <h3>{SECTION_TITLE}</h3>
      </div>
      <p className="muted small-text">{SECTION_SUBTITLE}</p>

      <EvidenceSummaryPanel
        campaignId={campaignId}
        contentId={contentId}
        distributed={distributed}
        refreshToken={summaryRefreshToken}
      />

      {!showCreateForm && correctingId === null && (
        <button type="button" className="button primary" style={{ marginTop: 12 }} onClick={() => setShowCreateForm(true)}>
          Agregar métricas reportadas
        </button>
      )}

      {showCreateForm && (
        <EvidenceForm
          campaignId={campaignId}
          contentId={contentId}
          onCancel={() => setShowCreateForm(false)}
          onSaved={() => {
            setShowCreateForm(false);
            reload();
          }}
        />
      )}

      <div className="section-heading" style={{ marginTop: 20 }}>
        <h4>Historial</h4>
      </div>

      {state.status === "loading" && (
        <p className="muted small-text" role="status">
          Cargando métricas reportadas…
        </p>
      )}

      {state.status === "error" && (
        <p className="small-text" role="alert">
          {state.message}{" "}
          <button type="button" className="auth-text-button" onClick={reload}>
            Reintentar
          </button>
        </p>
      )}

      {state.status === "ready" &&
        (state.items.length === 0 ? (
          <p className="muted small-text">{EMPTY_COPY}</p>
        ) : (
          <div className="deliverables-grid">
            {state.items.map((evidence) =>
              correctingId === evidence.id ? (
                <EvidenceForm
                  key={evidence.id}
                  campaignId={campaignId}
                  contentId={contentId}
                  target={evidence}
                  onCancel={() => setCorrectingId(null)}
                  onSaved={() => {
                    setCorrectingId(null);
                    reload();
                  }}
                />
              ) : (
                <EvidenceCard
                  key={evidence.id}
                  evidence={evidence}
                  correcting={correctingId !== null}
                  onCorrect={() => {
                    setShowCreateForm(false);
                    setCorrectingId(evidence.id);
                  }}
                />
              ),
            )}
          </div>
        ))}
    </section>
  );
}
