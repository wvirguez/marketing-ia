"use client";

// MVP-11D-B: campaign-level Measurement Analysis panel — explicit trigger
// for the deterministic analysis pipeline (apps/api/app/measurement/
// analysis_pipeline.py, exposed via POST /campaigns/{id}/analysis/run,
// MVP-11C-A/-R1/-B) plus a read of its persisted evidence
// (GET /campaigns/{id}/analysis). Rendered as a sibling of MetricsPanel
// inside the same "Métricas" tab — never merged into it, never a new tab
// (MVP-11D-A §H/§I).
//
// Hard invariants preserved throughout: METRIC ENTRY != PERFORMANCE
// OBSERVATION != PERFORMANCE SIGNAL != ANALYSIS RESULT != LEARNING
// CANDIDATE. This panel never triggers automatically — not on mount, not
// on campaign activation, not after a metric is created, not on
// `refreshToken` change. The trigger is synchronous: no polling, no
// setInterval, no background refetch loop, no fake progress percentage,
// no agent-stage visualization. RUNNING is rendered as a static, truthful
// notice, never as live progress.
//
// CLIENT_REQUEST_ID LIFECYCLE (MVP-11D-A-R1, frozen): a single ref holds
// either an active, reusable key or `null`. NETWORK_ERROR/500/401/403/422
// all preserve the current key (it was never consumed, or the outcome is
// genuinely ambiguous) — the next deliberate click reuses it. 409
// (IDEMPOTENCY_KEY_CONFLICT), COMPLETED, FAILED, and RUNNING all clear it
// (409/COMPLETED/FAILED are terminal-and-resolved; RUNNING refers to an
// operation this click did not create and cannot resolve) — the next
// deliberate click always mints a fresh key in those cases.
//
// SESSION EPISTEMICS (MVP-11D-A-R1, frozen): there is no GET-run endpoint,
// so "was analysis ever executed before this mount" is unknowable. An
// empty GET /analysis is rendered with neutral copy unless *this mounted
// session itself* just observed a COMPLETED response immediately followed
// by an empty refetch — that specific, transient, session-only fact is
// tracked in `triggerResult`, never inferred from GET /analysis alone,
// and never persisted (no localStorage/sessionStorage/URL/cookie).

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { ApiError } from "@/lib/api/client";
import { getCampaignAnalysis, triggerCampaignAnalysis } from "@/lib/api/measurement";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { AnalysisResponse } from "@/types/measurement";

type AnalysisState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: AnalysisResponse };

type TriggerResult =
  | { kind: "idle" }
  | { kind: "error"; message: string }
  | { kind: "completed" }
  | { kind: "failed"; failureReason: string }
  | { kind: "running" };

const NEUTRAL_EMPTY_TITLE = "No hay resultados de análisis disponibles con los datos actuales.";
const NEUTRAL_EMPTY_SECONDARY = "Puedes analizar las métricas registradas para generar resultados.";
const COMPLETED_EMPTY_COPY =
  "El análisis se ejecutó, pero no encontró observaciones ni señales con los datos actuales.";
const COMPLETED_SUCCESS_COPY = "Análisis actualizado con las métricas disponibles.";
const RUNNING_COPY = "Hay un análisis en curso para esta campaña. Esta página no se actualiza automáticamente.";
const CONFLICT_COPY = "No se pudo iniciar el análisis en este momento. Intenta de nuevo.";
const GENERIC_FAILURE_FALLBACK = "El análisis no se pudo completar.";
const REFETCH_FAILURE_COPY = "No pudimos actualizar los resultados del análisis.";
const NO_SIGNALS_COPY = "No se detectaron señales comparativas con los datos actuales.";
const NO_RESULT_COPY = "No se generó un resultado de análisis para esta ejecución.";

function isConflictError(error: unknown): error is ApiError {
  return error instanceof ApiError && error.code === "IDEMPOTENCY_KEY_CONFLICT";
}

function ObservationCard({ metricName, value }: { metricName: string; value: string | number }) {
  return (
    <article className="panel deliverable-card">
      <span className="deliverable-icon">
        <Icon name="chart" size={19} />
      </span>
      <div>
        <h3>{metricName}</h3>
        <p className="muted small-text">{String(value)}</p>
      </div>
    </article>
  );
}

function SignalCard({ summary }: { summary: string }) {
  return (
    <article className="panel deliverable-card">
      <span className="deliverable-icon">
        <Icon name="chart" size={19} />
      </span>
      <div>
        <p>{summary}</p>
      </div>
    </article>
  );
}

function AnalysisResultCard({ summary }: { summary: string }) {
  return (
    <article className="panel deliverable-card">
      <span className="deliverable-icon">
        <Icon name="chart" size={19} />
      </span>
      <div>
        <p>{summary}</p>
      </div>
    </article>
  );
}

function AnalysisEvidence({ data, showCompletedEmptyCopy }: { data: AnalysisResponse; showCompletedEmptyCopy: boolean }) {
  const isFullyEmpty = data.observations.length === 0 && data.signals.length === 0 && data.analysis_results.length === 0;

  if (isFullyEmpty) {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="chart" size={32} />
        </span>
        {showCompletedEmptyCopy ? (
          <h3>{COMPLETED_EMPTY_COPY}</h3>
        ) : (
          <>
            <h3>{NEUTRAL_EMPTY_TITLE}</h3>
            <p className="muted small-text">{NEUTRAL_EMPTY_SECONDARY}</p>
          </>
        )}
      </section>
    );
  }

  return (
    <>
      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Observaciones</h2>
      </div>
      {data.observations.length === 0 ? (
        <p className="muted small-text">Sin observaciones registradas para esta ejecución.</p>
      ) : (
        <div className="deliverables-grid">
          {data.observations.map((observation) => (
            <ObservationCard key={observation.id} metricName={observation.metric_name} value={observation.value} />
          ))}
        </div>
      )}

      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Señales</h2>
      </div>
      {data.signals.length === 0 ? (
        <p className="muted small-text">{NO_SIGNALS_COPY}</p>
      ) : (
        <div className="deliverables-grid">
          {data.signals.map((signal) => (
            <SignalCard key={signal.id} summary={signal.summary} />
          ))}
        </div>
      )}

      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Resultado del análisis</h2>
      </div>
      {data.analysis_results.length === 0 ? (
        <p className="muted small-text">{NO_RESULT_COPY}</p>
      ) : (
        <div className="deliverables-grid">
          {data.analysis_results.map((result) => (
            <AnalysisResultCard key={result.id} summary={result.summary} />
          ))}
        </div>
      )}
    </>
  );
}

export function AnalysisPanel({
  campaignId,
  active,
  refreshToken,
}: {
  campaignId: string;
  active: boolean;
  refreshToken: number;
}) {
  const [analysisState, setAnalysisState] = useState<AnalysisState>({ status: "loading" });
  const [triggerResult, setTriggerResult] = useState<TriggerResult>({ kind: "idle" });
  const [submitting, setSubmitting] = useState(false);
  const [refetchError, setRefetchError] = useState("");
  const requestedTokenRef = useRef<number | null>(null);
  const clientRequestIdRef = useRef<string | null>(null);

  function loadAnalysis(): Promise<AnalysisResponse> {
    return getCampaignAnalysis(campaignId);
  }

  function retryInitialLoad() {
    setAnalysisState({ status: "loading" });
    loadAnalysis()
      .then((data) => setAnalysisState({ status: "ready", data }))
      .catch((error) => setAnalysisState({ status: "error", message: describeCampaignError(error) }));
  }

  function retryRefetch() {
    setRefetchError("");
    loadAnalysis()
      .then((data) => setAnalysisState({ status: "ready", data }))
      .catch((error) => setRefetchError(describeCampaignError(error)));
  }

  useEffect(() => {
    if (!active || requestedTokenRef.current === refreshToken) return;
    let cancelled = false;
    requestedTokenRef.current = refreshToken;
    loadAnalysis()
      .then((data) => {
        if (!cancelled) setAnalysisState({ status: "ready", data });
      })
      .catch((error) => {
        if (!cancelled) {
          requestedTokenRef.current = null;
          setAnalysisState({ status: "error", message: describeCampaignError(error) });
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, campaignId, refreshToken]);

  async function handleTrigger() {
    if (submitting) return;
    setSubmitting(true);
    setRefetchError("");
    const key = clientRequestIdRef.current ?? crypto.randomUUID();
    clientRequestIdRef.current = key;

    try {
      const run = await triggerCampaignAnalysis(campaignId, { client_request_id: key });

      if (run.status === "COMPLETED") {
        clientRequestIdRef.current = null; // terminal, confirmed — next click mints a fresh key
        setTriggerResult({ kind: "completed" });
        try {
          const data = await loadAnalysis();
          setAnalysisState({ status: "ready", data });
        } catch (refetchErr) {
          // Truthful degradation (MVP-11D-B §27): the trigger really did
          // complete — never demoted to FAILED merely because the
          // subsequent read failed. Previously-loaded analysisState (if
          // any) is left completely untouched; no evidence is fabricated.
          setRefetchError(describeCampaignError(refetchErr));
        }
      } else if (run.status === "FAILED") {
        clientRequestIdRef.current = null; // terminal, confirmed — next click mints a fresh key
        setTriggerResult({ kind: "failed", failureReason: run.failure_reason ?? GENERIC_FAILURE_FALLBACK });
      } else {
        // RUNNING: refers to an operation this click did not create and
        // cannot resolve — never polled, never auto-resumed. The key is
        // still cleared so the next *new*, deliberate action mints its
        // own fresh key rather than silently continuing this one.
        clientRequestIdRef.current = null;
        setTriggerResult({ kind: "running" });
      }
    } catch (error) {
      if (isConflictError(error)) {
        // Definitive, permanent rejection of this key for this campaign
        // (MVP-11D-A-R1 §I) — never reused, never silently retried.
        clientRequestIdRef.current = null;
        setTriggerResult({ kind: "error", message: CONFLICT_COPY });
      } else {
        // NETWORK_ERROR/500/401/403/422: the key was never consumed (or
        // the outcome is genuinely ambiguous) — preserved untouched so a
        // deliberate retry reuses it.
        setTriggerResult({ kind: "error", message: describeCampaignError(error) });
      }
    } finally {
      setSubmitting(false);
    }
  }

  const showCompletedEmptyCopy = triggerResult.kind === "completed";

  return (
    <>
      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Análisis de métricas</h2>
      </div>

      <section className="panel">
        <p className="muted small-text">
          Este análisis evalúa las métricas actualmente registradas para esta campaña. No genera contenido, no
          modifica la estrategia ni produce recomendaciones automáticas.
        </p>
        <div className="composer-footer">
          <p role="status" aria-live="polite" aria-atomic="true">
            {submitting
              ? "Ejecutando el análisis con las métricas actuales…"
              : triggerResult.kind === "completed"
                ? COMPLETED_SUCCESS_COPY
                : ""}
          </p>
          <button
            type="button"
            className="button primary"
            aria-busy={submitting}
            disabled={submitting}
            onClick={handleTrigger}
          >
            {submitting ? "Analizando…" : "Analizar métricas"}
          </button>
        </div>

        {triggerResult.kind === "error" && (
          <p role="alert" style={{ marginTop: 12 }}>
            {triggerResult.message}
          </p>
        )}
        {triggerResult.kind === "failed" && (
          <p role="alert" style={{ marginTop: 12 }}>
            {triggerResult.failureReason}
          </p>
        )}
        {triggerResult.kind === "running" && (
          <p role="status" style={{ marginTop: 12 }}>
            {RUNNING_COPY}
          </p>
        )}
        {refetchError && (
          <p role="alert" style={{ marginTop: 12 }}>
            {REFETCH_FAILURE_COPY}{" "}
            <button type="button" className="auth-text-button" onClick={retryRefetch}>
              Reintentar
            </button>
          </p>
        )}
      </section>

      {analysisState.status === "loading" && (
        <section className="panel" style={{ marginTop: 24 }}>
          <p className="muted small-text" role="status">
            Cargando análisis…
          </p>
        </section>
      )}

      {analysisState.status === "error" && (
        <section className="panel workspace-empty" style={{ marginTop: 24 }}>
          <span className="workspace-empty-symbol">
            <Icon name="chart" size={32} />
          </span>
          <h3>No pudimos cargar el análisis en este momento.</h3>
          <p>
            {analysisState.message}{" "}
            <button type="button" className="auth-text-button" onClick={retryInitialLoad}>
              Reintentar
            </button>
          </p>
        </section>
      )}

      {analysisState.status === "ready" && (
        <AnalysisEvidence data={analysisState.data} showCompletedEmptyCopy={showCompletedEmptyCopy} />
      )}
    </>
  );
}
