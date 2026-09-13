"use client";

// MVP-12C: campaign-level Learning panel — explicit trigger for the
// Measurement -> Learning bridge (apps/api/app/learning/service.py,
// exposed via POST /campaigns/{id}/learning/derive, MVP-12B) plus a read
// of its persisted evidence (GET /campaigns/{id}/learning). Rendered as
// its own "Aprendizajes" tab, immediately after "Métricas" — never merged
// into it (MVP-12C-A §H).
//
// Hard invariant preserved throughout: only LearningCandidateStatus.VALIDATED
// may ever be labeled as validated learning in this UI. CANDIDATE_IDENTIFIED
// (the only status the current production bridge can ever produce) is
// rendered as "Candidato de aprendizaje" — never "aprendizaje validado",
// "hallazgo", "insight", or "conclusión validada".
//
// This panel never triggers automatically — not on mount, not on campaign
// activation, not on `refreshToken` change. The trigger is synchronous: no
// polling, no setInterval, no optimistic fake candidate, no client-side
// idempotency key (the backend's own bridge is deliberately keyless and
// idempotent by construction — MVP-12B-A-R1).
//
// SESSION EPISTEMICS: there is no way for this panel to know, from GET
// /learning alone, whether "no candidates" means no analysis exists yet,
// analysis exists but hasn't been derived, or derivation already ran and
// found nothing — the panel does not call GET /analysis to disambiguate
// (MVP-12C-A §N). An empty GET is rendered with neutral copy unless *this
// mounted session itself* just observed a successful derive response that
// was itself empty — that specific, transient, session-only fact is
// cleared again the moment any OTHER, independent GET succeeds (initial
// load, retry, or a refreshToken-driven reload), never persisted (no
// localStorage/sessionStorage/URL/cookie), and never inferred from GET
// /learning alone.
//
// No StrategicRecommendation UI: `strategic_recommendation_candidates` is
// always empty in current production (no code path creates one), so
// rendering any UI for it here would be dead code with nothing honest to
// show (MVP-12C-A §Q). No PATCH decision helper is added.

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { deriveCampaignLearning, getCampaignLearning } from "@/lib/api/learning";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { formatCampaignDate } from "@/lib/campaigns/status";
import type { LearningCandidatePublic, LearningCandidateStatus, LearningResponse } from "@/types/learning";

type LearningState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: LearningResponse };

type DeriveResult = { kind: "idle" } | { kind: "error"; message: string } | { kind: "completed-empty" };

const NEUTRAL_EMPTY_TITLE = "Aún no hay candidatos de aprendizaje para esta campaña.";
const NEUTRAL_EMPTY_SECONDARY =
  "Genera candidatos de aprendizaje a partir de los resultados de análisis registrados. Si aún no hay resultados de análisis, no se generará ningún candidato.";
const COMPLETED_EMPTY_TITLE = "El proceso se completó, pero no se generaron candidatos de aprendizaje.";
const COMPLETED_EMPTY_SECONDARY =
  "Esto puede deberse a que aún no hay resultados de análisis registrados para esta campaña.";
const CTA_LABEL = "Generar candidatos de aprendizaje";
const CTA_HELP_TEXT =
  "Identifica candidatos de aprendizaje a partir de los resultados de análisis ya registrados para esta campaña. No valida automáticamente ningún aprendizaje ni modifica la estrategia.";
const CTA_ACTIVE_LABEL = "Generando…";
const CTA_ACTIVE_STATUS = "Generando candidatos de aprendizaje con los resultados de análisis actuales…";

const STATUS_LABELS: Record<LearningCandidateStatus, string> = {
  CANDIDATE_IDENTIFIED: "Candidato de aprendizaje",
  PROVISIONAL: "Aprendizaje provisional",
  VALIDATION_PENDING: "Pendiente de validación",
  VALIDATED: "Aprendizaje validado",
  REJECTED: "Rechazado",
  INSUFFICIENT_EVIDENCE: "Evidencia insuficiente",
};

function CandidateCard({ candidate }: { candidate: LearningCandidatePublic }) {
  return (
    <article className="panel deliverable-card">
      <span className="deliverable-icon">
        <Icon name="chart" size={19} />
      </span>
      <div>
        <p>{candidate.summary}</p>
        <p className="muted small-text">{STATUS_LABELS[candidate.status]}</p>
        <p className="muted small-text">{formatCampaignDate(candidate.created_at)}</p>
      </div>
    </article>
  );
}

function LearningEvidence({ data, showCompletedEmptyCopy }: { data: LearningResponse; showCompletedEmptyCopy: boolean }) {
  if (data.learning_candidates.length === 0) {
    return (
      <section className="panel workspace-empty" style={{ marginTop: 24 }}>
        <span className="workspace-empty-symbol">
          <Icon name="chart" size={32} />
        </span>
        {showCompletedEmptyCopy ? (
          <>
            <h3>{COMPLETED_EMPTY_TITLE}</h3>
            <p className="muted small-text">{COMPLETED_EMPTY_SECONDARY}</p>
          </>
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
        <h2>Candidatos de aprendizaje</h2>
      </div>
      <div className="deliverables-grid">
        {data.learning_candidates.map((candidate) => (
          <CandidateCard key={candidate.id} candidate={candidate} />
        ))}
      </div>
    </>
  );
}

export function LearningPanel({
  campaignId,
  active,
  refreshToken,
}: {
  campaignId: string;
  active: boolean;
  refreshToken: number;
}) {
  const [learningState, setLearningState] = useState<LearningState>({ status: "loading" });
  const [deriveResult, setDeriveResult] = useState<DeriveResult>({ kind: "idle" });
  const [submitting, setSubmitting] = useState(false);
  const requestedTokenRef = useRef<number | null>(null);

  function loadLearning(): Promise<LearningResponse> {
    return getCampaignLearning(campaignId);
  }

  function retryInitialLoad() {
    setLearningState({ status: "loading" });
    loadLearning()
      .then((data) => {
        setLearningState({ status: "ready", data });
        // An independent GET (not a derive result) always returns the
        // panel to the ordinary neutral empty state (MVP-12C-A §10/§9).
        setDeriveResult({ kind: "idle" });
      })
      .catch((error) => setLearningState({ status: "error", message: describeCampaignError(error) }));
  }

  useEffect(() => {
    if (!active || requestedTokenRef.current === refreshToken) return;
    let cancelled = false;
    requestedTokenRef.current = refreshToken;
    loadLearning()
      .then((data) => {
        if (!cancelled) {
          setLearningState({ status: "ready", data });
          setDeriveResult({ kind: "idle" });
        }
      })
      .catch((error) => {
        if (!cancelled) {
          requestedTokenRef.current = null;
          setLearningState({ status: "error", message: describeCampaignError(error) });
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, campaignId, refreshToken]);

  async function handleDerive() {
    if (submitting) return;
    setSubmitting(true);
    setDeriveResult({ kind: "idle" });

    try {
      const data = await deriveCampaignLearning(campaignId);
      // The derive response is the authoritative post-derive state —
      // used directly, never refetched, never appended to prior state
      // (MVP-12C-A §L/§M): this is what prevents visual duplication on
      // replay and guarantees the rendered list always matches server
      // truth exactly.
      setLearningState({ status: "ready", data });
      setDeriveResult(data.learning_candidates.length === 0 ? { kind: "completed-empty" } : { kind: "idle" });
    } catch (error) {
      // Truthful degradation: whatever evidence was already on screen is
      // left completely untouched — a failed derive never clears prior
      // candidates and never fabricates a FAILED domain object (none
      // exists in this contract).
      setDeriveResult({ kind: "error", message: describeCampaignError(error) });
    } finally {
      setSubmitting(false);
    }
  }

  const showCompletedEmptyCopy = deriveResult.kind === "completed-empty";

  return (
    <>
      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Aprendizajes</h2>
      </div>

      <section className="panel">
        <p className="muted small-text">{CTA_HELP_TEXT}</p>
        <div className="composer-footer">
          <p role="status" aria-live="polite" aria-atomic="true">
            {submitting ? CTA_ACTIVE_STATUS : ""}
          </p>
          <button
            type="button"
            className="button primary"
            aria-busy={submitting}
            disabled={submitting}
            onClick={handleDerive}
          >
            {submitting ? CTA_ACTIVE_LABEL : CTA_LABEL}
          </button>
        </div>

        {deriveResult.kind === "error" && (
          <p role="alert" style={{ marginTop: 12 }}>
            {deriveResult.message}
          </p>
        )}
      </section>

      {learningState.status === "loading" && (
        <section className="panel" style={{ marginTop: 24 }}>
          <p className="muted small-text" role="status">
            Cargando aprendizajes…
          </p>
        </section>
      )}

      {learningState.status === "error" && (
        <section className="panel workspace-empty" style={{ marginTop: 24 }}>
          <span className="workspace-empty-symbol">
            <Icon name="chart" size={32} />
          </span>
          <h3>No pudimos cargar los aprendizajes en este momento.</h3>
          <p>
            {learningState.message}{" "}
            <button type="button" className="auth-text-button" onClick={retryInitialLoad}>
              Reintentar
            </button>
          </p>
        </section>
      )}

      {learningState.status === "ready" && (
        <LearningEvidence data={learningState.data} showCompletedEmptyCopy={showCompletedEmptyCopy} />
      )}
    </>
  );
}
