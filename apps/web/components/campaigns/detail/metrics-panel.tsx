"use client";

// MVP-09B: campaign-level Measurement panel — manual MetricEntry entry +
// read history. Uses only GET/POST /campaigns/{id}/metrics (existing
// apps/api/app/measurement/router.py); PUT (correction) and GET /analysis
// are deliberately out of scope for this first slice.
//
// Hard invariants preserved throughout (apps/api/app/measurement/models.py,
// service.py): METRIC ENTRY != PERFORMANCE OBSERVATION != PERFORMANCE
// SIGNAL != ANALYSIS RESULT != LEARNING CANDIDATE. A successful POST means
// only that a MetricEntry was stored — nothing about analysis, learning,
// or a strategic decision. `source` is always "MANUAL" on create (no
// selector for IMPORTED/PLATFORM — no real import/platform ingestion
// workflow exists in the product today). `values` is an open string-keyed
// dict — this panel never imposes a closed metric-name vocabulary, a
// non-negative rule, a percentage/currency rule, or an integer-only rule,
// since the backend imposes none of those either. `is_current` is
// rendered as plain descriptive metadata ("registro actual para este
// período y canal"), never as "verified"/"approved"/"best"/"correct".
//
// This panel never imports, modifies, or reuses
// apps/web/lib/campaign-metrics.ts — that file is orphaned legacy code
// with a closed 11-key vocabulary inconsistent with the backend's actual
// open contract (MVP-09A §X); it is out of scope here.

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Icon } from "@/components/ui/icon";
import { createMetricEntry, getMetrics } from "@/lib/api/measurement";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { formatCampaignDate } from "@/lib/campaigns/status";
import type { MetricEntryPublic, MetricSource } from "@/types/measurement";

type HistoryState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; items: MetricEntryPublic[] };

type MetricRow = { id: number; name: string; value: string };

const EMPTY_COPY = "Aún no hay métricas registradas para esta campaña.";
const EMPTY_SECONDARY_COPY = "Registra manualmente los primeros datos de rendimiento de la campaña.";
const SUCCESS_COPY = "Métricas registradas.";
const CURRENT_COPY = "Registro actual para este período y canal";
const PRIOR_COPY = "Registro anterior";

const SOURCE_LABELS: Record<MetricSource, string> = {
  MANUAL: "Manual",
  IMPORTED: "Importado",
  PLATFORM: "Plataforma",
};

// Deliberately permissive: any finite decimal, negative or positive — the
// backend imposes no non-negative/percentage/currency/integer-only rule,
// so this panel invents none either (MVP-09B §5).
function isValidDecimal(value: string): boolean {
  return /^-?\d+(\.\d+)?$/.test(value.trim());
}

function newRow(id: number): MetricRow {
  return { id, name: "", value: "" };
}

function ManualEntryForm({ campaignId, onCreated }: { campaignId: string; onCreated: () => void }) {
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [channel, setChannel] = useState("");
  const [rows, setRows] = useState<MetricRow[]>([newRow(0)]);
  const nextRowId = useRef(1);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [submitError, setSubmitError] = useState("");
  const [submitSuccess, setSubmitSuccess] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // One stable id per logical submission (MVP-09B §13): reused across
  // retries of the same unresolved attempt, regenerated only after a
  // confirmed successful creation.
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
    if (!channel.trim()) errors.channel = "Ingresa un canal.";

    const seenNames = new Set<string>();
    for (const row of rows) {
      const name = row.name.trim();
      if (!name) {
        errors[`row-${row.id}-name`] = "Ingresa un nombre de métrica.";
      } else {
        const normalized = name.toLowerCase();
        if (seenNames.has(normalized)) {
          errors[`row-${row.id}-name`] = "Ya agregaste una métrica con este nombre.";
        }
        seenNames.add(normalized);
      }
      if (row.value.trim() === "") {
        errors[`row-${row.id}-value`] = "Ingresa un valor.";
      } else if (!isValidDecimal(row.value)) {
        errors[`row-${row.id}-value`] = "Ingresa un número válido (por ejemplo: 120 o -3.5).";
      }
    }
    return errors;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setSubmitSuccess(false);
    setSubmitError("");

    const errors = validate();
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    const values: Record<string, string> = {};
    for (const row of rows) values[row.name.trim()] = row.value.trim();

    setSubmitting(true);
    try {
      await createMetricEntry(campaignId, {
        period_start: periodStart,
        period_end: periodEnd,
        channel: channel.trim(),
        source: "MANUAL",
        client_request_id: clientRequestIdRef.current,
        values,
      });
      setPeriodStart("");
      setPeriodEnd("");
      setChannel("");
      setRows([newRow(nextRowId.current++)]);
      setFieldErrors({});
      setSubmitSuccess(true);
      // Confirmed success: safe to start a fresh idempotency key for the
      // next logical entry (MVP-09B §23).
      clientRequestIdRef.current = crypto.randomUUID();
      onCreated();
    } catch (error) {
      // Ambiguous failure: form values and client_request_id are both
      // retained untouched, so a user retry of this same submission
      // reuses the same idempotency key (MVP-09B §24).
      setSubmitError(describeCampaignError(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="panel" onSubmit={handleSubmit} noValidate>
      <div className="section-heading">
        <h2>Registrar métricas</h2>
      </div>

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <div>
          <label htmlFor="metric-period-start">Inicio del período</label>
          <br />
          <input
            id="metric-period-start"
            type="date"
            value={periodStart}
            onChange={(event) => setPeriodStart(event.target.value)}
          />
          {fieldErrors.periodStart && <p className="small-text" role="alert">{fieldErrors.periodStart}</p>}
        </div>
        <div>
          <label htmlFor="metric-period-end">Fin del período</label>
          <br />
          <input
            id="metric-period-end"
            type="date"
            value={periodEnd}
            onChange={(event) => setPeriodEnd(event.target.value)}
          />
          {fieldErrors.periodEnd && <p className="small-text" role="alert">{fieldErrors.periodEnd}</p>}
        </div>
        <div>
          <label htmlFor="metric-channel">Canal</label>
          <br />
          <input
            id="metric-channel"
            type="text"
            placeholder="Ej. Instagram"
            value={channel}
            onChange={(event) => setChannel(event.target.value)}
          />
          {fieldErrors.channel && <p className="small-text" role="alert">{fieldErrors.channel}</p>}
        </div>
      </div>

      <div className="section-heading" style={{ marginTop: 20 }}>
        <h3>Métricas</h3>
      </div>
      {rows.map((row, index) => (
        <div key={row.id} style={{ display: "flex", gap: 12, alignItems: "flex-start", marginTop: 10, flexWrap: "wrap" }}>
          <div>
            <label htmlFor={`metric-name-${row.id}`}>Nombre de la métrica</label>
            <br />
            <input
              id={`metric-name-${row.id}`}
              type="text"
              placeholder="Ej. impresiones"
              value={row.name}
              onChange={(event) => updateRow(row.id, { name: event.target.value })}
            />
            {fieldErrors[`row-${row.id}-name`] && (
              <p className="small-text" role="alert">{fieldErrors[`row-${row.id}-name`]}</p>
            )}
          </div>
          <div>
            <label htmlFor={`metric-value-${row.id}`}>Valor</label>
            <br />
            <input
              id={`metric-value-${row.id}`}
              type="text"
              inputMode="decimal"
              placeholder="Ej. 1200"
              value={row.value}
              onChange={(event) => updateRow(row.id, { value: event.target.value })}
            />
            {fieldErrors[`row-${row.id}-value`] && (
              <p className="small-text" role="alert">{fieldErrors[`row-${row.id}-value`]}</p>
            )}
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

      {submitError && (
        <p className="small-text" role="alert" style={{ marginTop: 16 }}>
          {submitError}
        </p>
      )}
      {submitSuccess && (
        <p className="small-text" role="status" style={{ marginTop: 16 }}>
          {SUCCESS_COPY}
        </p>
      )}

      <div style={{ marginTop: 16 }}>
        <button type="submit" className="button primary" disabled={submitting}>
          {submitting ? "Registrando…" : "Registrar métricas"}
        </button>
      </div>
    </form>
  );
}

function MetricEntryCard({ entry }: { entry: MetricEntryPublic }) {
  return (
    <article className="panel deliverable-card">
      <span className="deliverable-icon">
        <Icon name="chart" size={19} />
      </span>
      <div>
        <h3>
          {entry.period_start} – {entry.period_end}
        </h3>
        <p className="muted small-text">{entry.channel}</p>
        <p className="muted small-text">{SOURCE_LABELS[entry.source]}</p>
        <dl style={{ margin: "10px 0 0" }}>
          {Object.entries(entry.values).map(([name, value]) => (
            <div key={name}>
              <dt className="muted small-text">{name}</dt>
              <dd style={{ margin: 0 }}>{String(value)}</dd>
            </div>
          ))}
        </dl>
        <p className="muted small-text" style={{ marginTop: 10 }}>
          Creado el {formatCampaignDate(entry.created_at)}
        </p>
        <p className="muted small-text">{entry.is_current ? CURRENT_COPY : PRIOR_COPY}</p>
      </div>
    </article>
  );
}

export function MetricsPanel({
  campaignId,
  active,
  refreshToken,
}: {
  campaignId: string;
  active: boolean;
  refreshToken: number;
}) {
  const [state, setState] = useState<HistoryState>({ status: "loading" });
  const requestedKeyRef = useRef<string | null>(null);
  const [localVersion, setLocalVersion] = useState(0);
  const fetchKey = `${refreshToken}:${localVersion}`;

  // Manual retry (button click, not an effect) — no cancellation guard
  // needed for a one-off user-initiated action, matching every other
  // panel's own `retry` precedent. Repeats only the GET.
  function retry() {
    setState({ status: "loading" });
    requestedKeyRef.current = fetchKey;
    getMetrics(campaignId)
      .then((response) => setState({ status: "ready", items: response.items }))
      .catch((error) => {
        requestedKeyRef.current = null;
        setState({ status: "error", message: describeCampaignError(error) });
      });
  }

  useEffect(() => {
    if (!active || requestedKeyRef.current === fetchKey) return;
    let cancelled = false;
    requestedKeyRef.current = fetchKey;
    getMetrics(campaignId)
      .then((response) => {
        if (!cancelled) setState({ status: "ready", items: response.items });
      })
      .catch((error) => {
        if (!cancelled) {
          requestedKeyRef.current = null;
          setState({ status: "error", message: describeCampaignError(error) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [active, campaignId, fetchKey]);

  return (
    <>
      <ManualEntryForm campaignId={campaignId} onCreated={() => setLocalVersion((version) => version + 1)} />

      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Historial de métricas</h2>
      </div>

      {state.status === "loading" && (
        <section className="panel">
          <p className="muted small-text" role="status">
            Cargando métricas…
          </p>
        </section>
      )}

      {state.status === "error" && (
        <section className="panel workspace-empty">
          <span className="workspace-empty-symbol">
            <Icon name="chart" size={32} />
          </span>
          <h3>No pudimos cargar las métricas en este momento.</h3>
          <p>
            {state.message}{" "}
            <button type="button" className="auth-text-button" onClick={retry}>
              Reintentar
            </button>
          </p>
        </section>
      )}

      {state.status === "ready" &&
        (state.items.length === 0 ? (
          <section className="panel workspace-empty">
            <span className="workspace-empty-symbol">
              <Icon name="chart" size={32} />
            </span>
            <h3>{EMPTY_COPY}</h3>
            <p className="muted small-text">{EMPTY_SECONDARY_COPY}</p>
          </section>
        ) : (
          <div className="deliverables-grid">
            {state.items.map((entry) => (
              <MetricEntryCard key={entry.id} entry={entry} />
            ))}
          </div>
        ))}
    </>
  );
}
