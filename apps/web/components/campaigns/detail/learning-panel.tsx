"use client";

// MVP-12C: campaign-level Learning panel — explicit trigger for the
// Measurement -> Learning bridge (apps/api/app/learning/service.py,
// exposed via POST /campaigns/{id}/learning/derive, MVP-12B) plus a read
// of its persisted evidence (GET /campaigns/{id}/learning). Rendered as
// its own "Aprendizajes" tab, immediately after "Métricas" — never merged
// into it (MVP-12C-A §H).
//
// MVP-23B extends this panel with the governed human maturation path
// (mark-provisional / mark-validation-pending / decision / recommendation
// creation / recommendation decision) frozen by MVP-23A/MVP-23A-R1. The
// backend state machine (app/learning/transitions.py) remains sole
// authority — this panel only ever offers the action(s) legal from the
// server-returned status, and every mutation's own response entity is
// spliced back into local state verbatim (server truth, never a locally
// invented status/decision).
//
// Hard invariant preserved throughout: only LearningCandidateStatus.VALIDATED
// may ever be labeled as validated learning in this UI, and VALIDATED never
// implies causal proof, statistical significance, Hypothesis/Experiment
// validation, or Strategy approval. A governed decision here records only
// that an authorized human judged it so.
//
// Role gating (MVP-23A-R1 §N-§Q): sourced from the existing session
// (auth.session.membership.role via useAuth()) — the exact mechanism
// ContentDetailView already uses for its own OWNER/ADMIN-gated decision
// UI. Frontend visibility is UX only; the backend's own require_role
// remains the actual authority (a hidden button is not security).
//
// This panel never triggers automatically — not on mount, not on campaign
// activation, not on `refreshToken` change. The derive trigger is
// synchronous: no polling, no setInterval, no optimistic fake candidate.
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

import { useEffect, useRef, useState } from "react";
import { LearningQualification, type QualificationMutation } from "./learning-qualification";
import { updateLearningQualification, attachLearningEvidence, disposeLearningEvidence } from "@/lib/api/learning";
import { Icon } from "@/components/ui/icon";
import {
  createStrategicImplication,
  createStrategicRecommendation,
  decideLearningCandidate,
  decideStrategicRecommendation,
  deriveCampaignLearning,
  getCampaignLearning,
  markLearningCandidateProvisional,
  markLearningCandidateValidationPending,
} from "@/lib/api/learning";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { formatCampaignDate } from "@/lib/campaigns/status";
import { useAuth } from "@/lib/auth/auth-context";
import type {
  LearningCandidatePublic,
  LearningCandidateStatus,
  LearningResponse,
  StrategicImplicationPublic,
  StrategicRecommendationCandidatePublic,
  StrategicRecommendationDecision,
} from "@/types/learning";

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

const CANDIDATE_DECISION_LABELS: Record<Extract<LearningCandidateStatus, "VALIDATED" | "REJECTED" | "INSUFFICIENT_EVIDENCE">, string> = {
  VALIDATED: "Validar aprendizaje",
  REJECTED: "Rechazar",
  INSUFFICIENT_EVIDENCE: "Evidencia insuficiente",
};

const GOVERNANCE_NOTE =
  "Esta es una decisión de gobernanza humana sobre el candidato de aprendizaje; no implica una prueba causal ni significancia estadística.";

function isOwnerOrAdmin(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN";
}

function CandidateActions({
  candidate,
  role,
  pending,
  armedKey,
  onArm,
  onMarkProvisional,
  onMarkValidationPending,
  onDecide,
}: {
  candidate: LearningCandidatePublic;
  role: string | null;
  pending: boolean;
  armedKey: string | null;
  onArm: (key: string | null) => void;
  onMarkProvisional: (candidateId: string) => void;
  onMarkValidationPending: (candidateId: string) => void;
  onDecide: (candidateId: string, decision: keyof typeof CANDIDATE_DECISION_LABELS) => void;
}) {
  const canDecide = isOwnerOrAdmin(role);

  if (candidate.status === "CANDIDATE_IDENTIFIED") {
    return (
      <button type="button" className="button" disabled={pending} onClick={() => onMarkProvisional(candidate.id)}>
        Marcar como provisional
      </button>
    );
  }

  if (candidate.status === "PROVISIONAL") {
    return (
      <button type="button" className="button" disabled={pending} onClick={() => onMarkValidationPending(candidate.id)}>
        Enviar a validación
      </button>
    );
  }

  if (candidate.status === "INSUFFICIENT_EVIDENCE") {
    return (
      <button type="button" className="button" disabled={pending} onClick={() => onMarkValidationPending(candidate.id)}>
        Volver a enviar a validación
      </button>
    );
  }

  if (candidate.status === "VALIDATION_PENDING" && canDecide) {
    return (
      <div style={{ marginTop: 8 }}>
        <p className="muted small-text">{GOVERNANCE_NOTE}</p>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 8 }}>
          {(Object.keys(CANDIDATE_DECISION_LABELS) as (keyof typeof CANDIDATE_DECISION_LABELS)[]).map((decision) => {
            const key = `candidate-decision:${candidate.id}:${decision}`;
            return (
              <button
                key={decision}
                type="button"
                className="button"
                disabled={pending || (decision === "VALIDATED" && (!candidate.qualification || candidate.qualification.validation_blockers.length > 0))}
                onClick={() => {
                  if (armedKey !== key) {
                    onArm(key);
                    return;
                  }
                  onArm(null);
                  onDecide(candidate.id, decision);
                }}
              >
                {armedKey === key ? "Confirmar" : CANDIDATE_DECISION_LABELS[decision]}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  return null;
}

// MVP-26: an implication is a bounded, human-governed interpretation of a
// validated Learning — never itself a decision, never a Strategy mutation,
// never a causal or commercial claim. This copy is required to accompany
// every implication surface (MVP-26 §36).
const IMPLICATION_GOVERNANCE_NOTE =
  "Una implicación estratégica es una interpretación de gobernanza humana sobre un aprendizaje validado y suficientemente calificado. No es una decisión estratégica, no modifica la Estrategia por sí sola, no establece causalidad y no establece atribución comercial.";

const LEGACY_RECOMMENDATION_NOTE =
  "Recomendación histórica, creada antes de que se exigiera vincularla a una implicación estratégica.";

function StrategicImplicationForm({
  candidateId,
  pending,
  onCreate,
}: {
  candidateId: string;
  pending: boolean;
  onCreate: (candidateId: string, statement: string) => void;
}) {
  const [statement, setStatement] = useState("");

  function handleSubmit() {
    const trimmed = statement.trim();
    if (!trimmed) return;
    onCreate(candidateId, trimmed);
    setStatement("");
  }

  return (
    <div style={{ marginTop: 8 }}>
      <p className="muted small-text">{IMPLICATION_GOVERNANCE_NOTE}</p>
      <label htmlFor={`implication-statement-${candidateId}`} className="muted small-text">
        ¿Qué implica este aprendizaje validado para la estrategia, dentro de su alcance y límite de generalización?
      </label>
      <textarea
        id={`implication-statement-${candidateId}`}
        value={statement}
        onChange={(event) => setStatement(event.target.value)}
        disabled={pending}
        rows={2}
        style={{ width: "100%", marginTop: 4 }}
      />
      <button type="button" className="button" disabled={pending || statement.trim().length === 0} onClick={handleSubmit} style={{ marginTop: 6 }}>
        Registrar implicación estratégica
      </button>
    </div>
  );
}

function RecommendationFromImplicationForm({
  candidateId,
  implication,
  pending,
  onCreate,
}: {
  candidateId: string;
  implication: StrategicImplicationPublic;
  pending: boolean;
  onCreate: (candidateId: string, implicationId: string, summary: string) => void;
}) {
  const [summary, setSummary] = useState("");
  const [expanded, setExpanded] = useState(false);

  function handleSubmit() {
    const trimmed = summary.trim();
    if (!trimmed) return;
    onCreate(candidateId, implication.id, trimmed);
    setSummary("");
    setExpanded(false);
  }

  if (!expanded) {
    return (
      <button type="button" className="auth-text-button" disabled={pending} onClick={() => setExpanded(true)} style={{ marginTop: 6 }}>
        Crear recomendación a partir de esta implicación
      </button>
    );
  }

  return (
    <div style={{ marginTop: 8 }}>
      <label htmlFor={`recommendation-summary-${implication.id}`} className="muted small-text">
        Proponer una recomendación estratégica a partir de esta implicación
      </label>
      <textarea
        id={`recommendation-summary-${implication.id}`}
        value={summary}
        onChange={(event) => setSummary(event.target.value)}
        disabled={pending}
        rows={2}
        style={{ width: "100%", marginTop: 4 }}
      />
      <button type="button" className="button" disabled={pending || summary.trim().length === 0} onClick={handleSubmit} style={{ marginTop: 6 }}>
        Proponer recomendación
      </button>
    </div>
  );
}

function StrategicImplicationCard({
  candidateId,
  implication,
  pending,
  onCreateRecommendation,
}: {
  candidateId: string;
  implication: StrategicImplicationPublic;
  pending: boolean;
  onCreateRecommendation: (candidateId: string, implicationId: string, summary: string) => void;
}) {
  return (
    <article className="panel deliverable-card" style={{ marginTop: 8 }}>
      <div>
        <p>{implication.statement}</p>
        <p className="muted small-text">{formatCampaignDate(implication.created_at)}</p>
        <RecommendationFromImplicationForm
          candidateId={candidateId}
          implication={implication}
          pending={pending}
          onCreate={onCreateRecommendation}
        />
      </div>
    </article>
  );
}

const RECOMMENDATION_DECISION_LABELS: Record<StrategicRecommendationDecision, string> = {
  ACCEPTED: "Aceptar",
  REJECTED: "Rechazar",
};

function RecommendationCard({
  recommendation,
  role,
  pending,
  armedKey,
  onArm,
  onDecide,
}: {
  recommendation: StrategicRecommendationCandidatePublic;
  role: string | null;
  pending: boolean;
  armedKey: string | null;
  onArm: (key: string | null) => void;
  onDecide: (recommendationId: string, decision: StrategicRecommendationDecision) => void;
}) {
  const canDecide = isOwnerOrAdmin(role);
  return (
    <article className="panel deliverable-card" style={{ marginTop: 8 }}>
      <div>
        <p>{recommendation.summary}</p>
        <p className="muted small-text">
          {recommendation.decision === null
            ? "Recomendación estratégica candidata, pendiente de decisión"
            : `Recomendación ${recommendation.decision === "ACCEPTED" ? "aceptada" : "rechazada"}`}
        </p>
        {recommendation.strategic_implication_id === null && (
          <p className="muted small-text">{LEGACY_RECOMMENDATION_NOTE}</p>
        )}
        <p className="muted small-text">{formatCampaignDate(recommendation.created_at)}</p>
        {recommendation.decision === null && canDecide && (
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 8 }}>
            <p className="muted small-text" style={{ width: "100%" }}>
              Esta decisión registra una recomendación estratégica candidata; no crea ni modifica una Estrategia por sí sola.
            </p>
            {(Object.keys(RECOMMENDATION_DECISION_LABELS) as StrategicRecommendationDecision[]).map((decision) => {
              const key = `recommendation-decision:${recommendation.id}:${decision}`;
              return (
                <button
                  key={decision}
                  type="button"
                  className="button"
                  disabled={pending}
                  onClick={() => {
                    if (armedKey !== key) {
                      onArm(key);
                      return;
                    }
                    onArm(null);
                    onDecide(recommendation.id, decision);
                  }}
                >
                  {armedKey === key ? "Confirmar" : RECOMMENDATION_DECISION_LABELS[decision]}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </article>
  );
}

function CandidateCard({
  candidate,
  onQualification,
  recommendations,
  role,
  pending,
  armedKey,
  onArm,
  onMarkProvisional,
  onMarkValidationPending,
  onDecideCandidate,
  onCreateImplication,
  onCreateRecommendation,
  onDecideRecommendation,
}: {
  candidate: LearningCandidatePublic;
  recommendations: StrategicRecommendationCandidatePublic[];
  onQualification: (candidateId: string, mutation: QualificationMutation) => void;
  role: string | null;
  pending: boolean;
  armedKey: string | null;
  onArm: (key: string | null) => void;
  onMarkProvisional: (candidateId: string) => void;
  onMarkValidationPending: (candidateId: string) => void;
  onDecideCandidate: (candidateId: string, decision: keyof typeof CANDIDATE_DECISION_LABELS) => void;
  onCreateImplication: (candidateId: string, statement: string) => void;
  onCreateRecommendation: (candidateId: string, implicationId: string, summary: string) => void;
  onDecideRecommendation: (recommendationId: string, decision: StrategicRecommendationDecision) => void;
}) {
  return (
    <article className="panel deliverable-card">
      <span className="deliverable-icon">
        <Icon name="chart" size={19} />
      </span>
      <div>
        <p>{candidate.summary}</p>
        <p className="muted small-text">{STATUS_LABELS[candidate.status]}</p>
        <p className="muted small-text">{formatCampaignDate(candidate.created_at)}</p>

        <LearningQualification key={`${candidate.id}:${candidate.qualification?.updated_at ?? "none"}`} candidate={candidate} editable={role === "OWNER" || role === "ADMIN" || role === "MEMBER"} pending={pending} onMutate={onQualification} />
        <CandidateActions
          candidate={candidate}
          role={role}
          pending={pending}
          armedKey={armedKey}
          onArm={onArm}
          onMarkProvisional={onMarkProvisional}
          onMarkValidationPending={onMarkValidationPending}
          onDecide={onDecideCandidate}
        />

        {candidate.status === "VALIDATED" && (
          <>
            <StrategicImplicationForm candidateId={candidate.id} pending={pending} onCreate={onCreateImplication} />
            {candidate.strategic_implications.map((implication) => (
              <StrategicImplicationCard
                key={implication.id}
                candidateId={candidate.id}
                implication={implication}
                pending={pending}
                onCreateRecommendation={onCreateRecommendation}
              />
            ))}
          </>
        )}

        {recommendations.map((recommendation) => (
          <RecommendationCard
            key={recommendation.id}
            recommendation={recommendation}
            role={role}
            pending={pending}
            armedKey={armedKey}
            onArm={onArm}
            onDecide={onDecideRecommendation}
          />
        ))}
      </div>
    </article>
  );
}

function LearningEvidence({
  data,
  onQualification,
  showCompletedEmptyCopy,
  role,
  pending,
  armedKey,
  onArm,
  onMarkProvisional,
  onMarkValidationPending,
  onDecideCandidate,
  onCreateImplication,
  onCreateRecommendation,
  onDecideRecommendation,
}: {
  data: LearningResponse;
  showCompletedEmptyCopy: boolean;
  onQualification: (candidateId: string, mutation: QualificationMutation) => void;
  role: string | null;
  pending: boolean;
  armedKey: string | null;
  onArm: (key: string | null) => void;
  onMarkProvisional: (candidateId: string) => void;
  onMarkValidationPending: (candidateId: string) => void;
  onDecideCandidate: (candidateId: string, decision: keyof typeof CANDIDATE_DECISION_LABELS) => void;
  onCreateImplication: (candidateId: string, statement: string) => void;
  onCreateRecommendation: (candidateId: string, implicationId: string, summary: string) => void;
  onDecideRecommendation: (recommendationId: string, decision: StrategicRecommendationDecision) => void;
}) {
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
          <CandidateCard
            key={candidate.id}
            candidate={candidate}
            onQualification={onQualification}
            recommendations={data.strategic_recommendation_candidates.filter((r) => r.learning_candidate_id === candidate.id)}
            role={role}
            pending={pending}
            armedKey={armedKey}
            onArm={onArm}
            onMarkProvisional={onMarkProvisional}
            onMarkValidationPending={onMarkValidationPending}
            onDecideCandidate={onDecideCandidate}
            onCreateImplication={onCreateImplication}
            onCreateRecommendation={onCreateRecommendation}
            onDecideRecommendation={onDecideRecommendation}
          />
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
  const auth = useAuth();
  const role = auth.status === "authenticated" ? auth.session.membership.role : null;
  const [learningState, setLearningState] = useState<LearningState>({ status: "loading" });
  const [deriveResult, setDeriveResult] = useState<DeriveResult>({ kind: "idle" });
  const [submitting, setSubmitting] = useState(false);
  // MVP-23B §32/§33: panel-wide single-flight for every maturation/
  // recommendation mutation, independent of the derive CTA's own
  // `submitting` lock — mirrors ContentDetailView's `pending`/
  // `mutationError` shape exactly.
  const mutationLock = useRef(false);
  const [pending, setPending] = useState(false);
  const [mutationError, setMutationError] = useState("");
  const [armedKey, setArmedKey] = useState<string | null>(null);
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
    if (mutationLock.current) return;
    mutationLock.current = true;
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
      mutationLock.current = false;
      setSubmitting(false);
    }
  }

  // AUTHORITATIVE SERVER TRUTH (MVP-23B §31/§33): every mutation splices
  // ONLY the server-returned canonical entity into local state — never a
  // locally invented status/decision. A failure leaves the last confirmed
  // server state completely untouched and surfaces a local, actionable
  // error; it never rolls back to a synthetic prior status.
  async function runCandidateMutation(action: () => Promise<LearningCandidatePublic>) {
    if (mutationLock.current) return;
    mutationLock.current = true;
    setPending(true);
    setMutationError("");
    try {
      const updated = await action();
      setLearningState((prev) =>
        prev.status === "ready"
          ? { status: "ready", data: { ...prev.data, learning_candidates: prev.data.learning_candidates.map((c) => (c.id === updated.id ? updated : c)) } }
          : prev,
      );
    } catch (error) {
      setMutationError(describeCampaignError(error));
    } finally {
      mutationLock.current = false;
      setPending(false);
    }
  }

  async function runRecommendationDecisionMutation(action: () => Promise<StrategicRecommendationCandidatePublic>) {
    if (mutationLock.current) return;
    mutationLock.current = true;
    setPending(true);
    setMutationError("");
    try {
      const updated = await action();
      setLearningState((prev) =>
        prev.status === "ready"
          ? {
              status: "ready",
              data: {
                ...prev.data,
                strategic_recommendation_candidates: prev.data.strategic_recommendation_candidates.map((r) =>
                  r.id === updated.id ? updated : r,
                ),
              },
            }
          : prev,
      );
    } catch (error) {
      setMutationError(describeCampaignError(error));
    } finally {
      mutationLock.current = false;
      setPending(false);
    }
  }

  async function runImplicationCreation(candidateId: string, statement: string) {
    if (mutationLock.current) return;
    mutationLock.current = true;
    setPending(true);
    setMutationError("");
    try {
      const created = await createStrategicImplication(campaignId, candidateId, statement);
      setLearningState((prev) =>
        prev.status === "ready"
          ? {
              status: "ready",
              data: {
                ...prev.data,
                learning_candidates: prev.data.learning_candidates.map((c) =>
                  c.id === candidateId ? { ...c, strategic_implications: [...c.strategic_implications, created] } : c,
                ),
              },
            }
          : prev,
      );
    } catch (error) {
      setMutationError(describeCampaignError(error));
    } finally {
      mutationLock.current = false;
      setPending(false);
    }
  }

  async function runRecommendationCreation(candidateId: string, implicationId: string, summary: string) {
    if (mutationLock.current) return;
    mutationLock.current = true;
    setPending(true);
    setMutationError("");
    try {
      const created = await createStrategicRecommendation(campaignId, candidateId, implicationId, summary);
      setLearningState((prev) =>
        prev.status === "ready"
          ? {
              status: "ready",
              data: { ...prev.data, strategic_recommendation_candidates: [...prev.data.strategic_recommendation_candidates, created] },
            }
          : prev,
      );
    } catch (error) {
      setMutationError(describeCampaignError(error));
    } finally {
      mutationLock.current = false;
      setPending(false);
    }
  }

  async function handleQualification(candidateId: string, mutation: QualificationMutation) {
    if (mutationLock.current) return;
    mutationLock.current = true;
    setPending(true);
    setMutationError("");
    setArmedKey(null);
    let committed = false;
    try {
      if (mutation.kind === "update") await updateLearningQualification(campaignId, candidateId, mutation.body);
      else if (mutation.kind === "attach") await attachLearningEvidence(campaignId, candidateId, mutation.body);
      else await disposeLearningEvidence(campaignId, candidateId, mutation.signalId, mutation.body);
      committed = true;
      setLearningState({ status: "ready", data: await loadLearning() });
    } catch (error) {
      const message = describeCampaignError(error);
      if (committed) setLearningState({ status: "error", message });
      else setMutationError(message);
    } finally {
      mutationLock.current = false;
      setPending(false);
    }
  }

  function handleMarkProvisional(candidateId: string) {
    runCandidateMutation(() => markLearningCandidateProvisional(campaignId, candidateId));
  }

  function handleMarkValidationPending(candidateId: string) {
    runCandidateMutation(() => markLearningCandidateValidationPending(campaignId, candidateId));
  }

  function handleDecideCandidate(candidateId: string, decision: keyof typeof CANDIDATE_DECISION_LABELS) {
    runCandidateMutation(() => decideLearningCandidate(campaignId, candidateId, decision));
  }

  function handleDecideRecommendation(recommendationId: string, decision: StrategicRecommendationDecision) {
    runRecommendationDecisionMutation(() => decideStrategicRecommendation(campaignId, recommendationId, decision));
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
            disabled={submitting || pending}
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

      {mutationError && (
        <p role="alert" style={{ marginTop: 12 }}>
          {mutationError}
        </p>
      )}

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
        <LearningEvidence
          onQualification={handleQualification}
          data={learningState.data}
          showCompletedEmptyCopy={showCompletedEmptyCopy}
          role={role}
          pending={pending || submitting}
          armedKey={armedKey}
          onArm={setArmedKey}
          onMarkProvisional={handleMarkProvisional}
          onMarkValidationPending={handleMarkValidationPending}
          onDecideCandidate={handleDecideCandidate}
          onCreateImplication={runImplicationCreation}
          onCreateRecommendation={runRecommendationCreation}
          onDecideRecommendation={handleDecideRecommendation}
        />
      )}
    </>
  );
}
