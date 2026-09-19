"use client";

// MVP-05C: real Plan tab, extended by MVP-33B with governed creation.
// Same lazy-fetch/cache/invalidate lifecycle as
// ResearchPanel/AudiencePanel/StrategyPanel.
//
// READY FOR PLANNING != READY FOR PRODUCTION: PlanItemPublic carries no
// approval/readiness/production field on the backend, so none is
// fabricated here — no "Aprobado"/"Listo para publicar" badge exists in
// this panel. Experiment provenance (MVP-33A §AA) makes exactly one claim
// — "this plan operationalizes that Experiment" — never Variant,
// execution, or measurement readiness; none of that language appears here.

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { useAuth } from "@/lib/auth/auth-context";
import { createContentPiece } from "@/lib/api/content";
import { createBrief, createPlan, getPlan } from "@/lib/api/planning";
import { getStrategy } from "@/lib/api/strategy";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { PlanOutputResponse } from "@/types/planning";
import type { ExperimentPublic } from "@/types/strategy";
import { DraftDisclosureBanner } from "./draft-disclosure-banner";

type Result =
  | { token: number; status: "error"; message: string }
  | { token: number; status: "ready"; output: PlanOutputResponse };

const BEFORE_START_COPY = "Aún no se ha generado el borrador de planificación para esta campaña.";
const EMPTY_ITEMS_COPY = "Este borrador de planificación aún no contiene elementos.";

// MVP-33A §U: same MEMBER+ authority tier as Hypothesis/Experiment
// creation — a Content Plan proposes, it never commits or mutates
// authoritative Strategy/Hypothesis/Experiment state.
function canProposePlan(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN" || role === "MEMBER";
}

// MVP-34A §S: same MEMBER+ tier — a Content Brief proposes planning-
// adjacent content, it never approves or authorizes production.
function canProposeBrief(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN" || role === "MEMBER";
}

// MVP-35A §R: same MEMBER+ tier — a Content Piece proposes/records
// production content, it is not a Content Approval decision.
function canProposePiece(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN" || role === "MEMBER";
}

const EMPTY_PIECE_FORM = { format: "", objective: "", funnelStage: "", cta: "", channel: "", payload: "" };

export function PlanPanel({
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

  const [result, setResult] = useState<Result | null>(null);
  const requestedTokenRef = useRef<number | null>(null);

  const [showPlanForm, setShowPlanForm] = useState(false);
  const [planSummary, setPlanSummary] = useState("");
  // MVP-33A-R1 §R (F1): populated only from the existing GET /strategy
  // surface — current-Strategy Experiments only. Historical Experiments
  // remain backend-eligible but are not selectable here
  // (MVP33A-R1-OBS-1).
  const [currentExperiments, setCurrentExperiments] = useState<ExperimentPublic[]>([]);
  // MVP-33A §26: never auto-selected — "" means no provenance claim.
  const [selectedExperimentId, setSelectedExperimentId] = useState("");
  const [planPending, setPlanPending] = useState(false);
  const [planError, setPlanError] = useState("");

  // MVP-34A/-34B: at most one open Brief form at a time, keyed by the
  // target PlanItem's public id — mirrors the single-form shape
  // showPlanForm already establishes one level up.
  const [briefFormItemId, setBriefFormItemId] = useState<string | null>(null);
  const [briefText, setBriefText] = useState("");
  const [briefPending, setBriefPending] = useState(false);
  const [briefError, setBriefError] = useState("");

  // MVP-35A/-35B: at most one open Piece form at a time, keyed by the
  // owning PlanItem's public id — mirrors the Brief form's own shape.
  // ContentBrief 1 -> 0..N ContentPiece (MVP-35A §D): repeated creation
  // is legitimate, so no "already has a piece" gate exists here.
  const [pieceFormItemId, setPieceFormItemId] = useState<string | null>(null);
  const [pieceForm, setPieceForm] = useState(EMPTY_PIECE_FORM);
  const [piecePending, setPiecePending] = useState(false);
  const [pieceError, setPieceError] = useState("");
  // Per-item, cleared whenever that item's form reopens — proof of
  // success without fabricating a new ContentBrief.pieces relationship
  // (MVP-35A §20/§AA: the canonical readback surface remains
  // GET /content and GET /content/{id}, not this panel).
  const [pieceCreatedByItemId, setPieceCreatedByItemId] = useState<Record<string, string>>({});

  function retry() {
    requestedTokenRef.current = refreshToken;
    getPlan(campaignId)
      .then((output) => setResult({ token: refreshToken, status: "ready", output }))
      .catch((error) => {
        requestedTokenRef.current = null;
        setResult({ token: refreshToken, status: "error", message: describeCampaignError(error) });
      });
  }

  async function submitPlan() {
    if (planPending || planSummary.trim().length === 0) return;
    setPlanPending(true);
    setPlanError("");
    try {
      await createPlan(campaignId, planSummary.trim(), selectedExperimentId || null);
      setPlanSummary("");
      setSelectedExperimentId("");
      setShowPlanForm(false);
      retry();
    } catch (error) {
      setPlanError(describeCampaignError(error));
    } finally {
      setPlanPending(false);
    }
  }

  async function submitBrief(planItemId: string) {
    if (briefPending || briefText.trim().length === 0) return;
    setBriefPending(true);
    setBriefError("");
    try {
      await createBrief(campaignId, planItemId, briefText.trim());
      setBriefText("");
      setBriefFormItemId(null);
      retry();
    } catch (error) {
      setBriefError(describeCampaignError(error));
    } finally {
      setBriefPending(false);
    }
  }

  function pieceFormIsFillable(): boolean {
    return (
      pieceForm.format.trim().length > 0 &&
      pieceForm.objective.trim().length > 0 &&
      pieceForm.funnelStage.trim().length > 0 &&
      pieceForm.cta.trim().length > 0 &&
      pieceForm.channel.trim().length > 0
    );
  }

  async function submitPiece(planItemId: string, contentBriefId: string) {
    if (piecePending || !pieceFormIsFillable()) return;
    let payload: Record<string, unknown> = {};
    if (pieceForm.payload.trim().length > 0) {
      try {
        payload = JSON.parse(pieceForm.payload);
      } catch {
        setPieceError("El contenido debe ser un objeto JSON válido (o dejarse en blanco).");
        return;
      }
    }
    setPiecePending(true);
    setPieceError("");
    try {
      const detail = await createContentPiece(campaignId, contentBriefId, {
        format: pieceForm.format.trim(),
        objective: pieceForm.objective.trim(),
        funnel_stage: pieceForm.funnelStage.trim(),
        cta: pieceForm.cta.trim(),
        channel: pieceForm.channel.trim(),
        payload,
      });
      setPieceForm(EMPTY_PIECE_FORM);
      setPieceFormItemId(null);
      setPieceCreatedByItemId((prev) => ({ ...prev, [planItemId]: detail.piece.id }));
    } catch (error) {
      setPieceError(describeCampaignError(error));
    } finally {
      setPiecePending(false);
    }
  }

  useEffect(() => {
    if (!active || requestedTokenRef.current === refreshToken) return;
    let cancelled = false;
    requestedTokenRef.current = refreshToken;
    getPlan(campaignId)
      .then((output) => {
        if (!cancelled) setResult({ token: refreshToken, status: "ready", output });
      })
      .catch((error) => {
        if (!cancelled) {
          requestedTokenRef.current = null;
          setResult({ token: refreshToken, status: "error", message: describeCampaignError(error) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [active, campaignId, refreshToken]);

  useEffect(() => {
    if (!showPlanForm) return;
    let cancelled = false;
    getStrategy(campaignId)
      .then((output) => {
        if (!cancelled) setCurrentExperiments(output.experiments);
      })
      .catch(() => {
        if (!cancelled) setCurrentExperiments([]);
      });
    return () => {
      cancelled = true;
    };
  }, [showPlanForm, campaignId]);

  const loading = result === null || result.token !== refreshToken;

  if (loading) {
    return (
      <section className="panel">
        <p className="muted small-text" role="status">
          Cargando plan…
        </p>
      </section>
    );
  }

  if (result.status === "error") {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="calendar" size={32} />
        </span>
        <h2>No pudimos cargar este borrador en este momento.</h2>
        <p>
          {result.message}{" "}
          <button type="button" className="auth-text-button" onClick={retry}>
            Reintentar
          </button>
        </p>
      </section>
    );
  }

  const { plan, items } = result.output;

  const planForm = canProposePlan(role) && (
    <div style={{ marginTop: 16 }}>
      {!showPlanForm ? (
        <button
          type="button"
          className="button"
          onClick={() => {
            setShowPlanForm(true);
            setPlanSummary("");
            setSelectedExperimentId("");
            setPlanError("");
          }}
        >
          Proponer plan
        </button>
      ) : (
        <div className="panel">
          <div className="settings-field">
            <label htmlFor="plan-summary">Nuevo plan</label>
            <textarea
              id="plan-summary"
              value={planSummary}
              disabled={planPending}
              onChange={(event) => setPlanSummary(event.target.value)}
            />
          </div>
          <div className="settings-field">
            <label htmlFor="plan-experiment">Experimento que operacionaliza este plan (opcional)</label>
            <select
              id="plan-experiment"
              value={selectedExperimentId}
              disabled={planPending}
              onChange={(event) => setSelectedExperimentId(event.target.value)}
            >
              <option value="">Sin experimento (plan genérico)</option>
              {currentExperiments.map((experiment) => (
                <option key={experiment.id} value={experiment.id}>
                  {experiment.id} — {experiment.description}
                </option>
              ))}
            </select>
          </div>
          <p className="muted small-text">
            Este plan se registra para la campaña — no crea briefs ni piezas de contenido, y no autoriza
            ejecución externa.
          </p>
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="button primary"
              disabled={planPending || planSummary.trim().length === 0}
              onClick={submitPlan}
            >
              Confirmar plan
            </button>
            <button
              type="button"
              className="button"
              disabled={planPending}
              onClick={() => {
                setShowPlanForm(false);
                setPlanSummary("");
                setSelectedExperimentId("");
              }}
            >
              Cancelar
            </button>
          </div>
          {planError && (
            <p role="alert" className="settings-feedback">
              {planError}
            </p>
          )}
        </div>
      )}
    </div>
  );

  if (!plan) {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="calendar" size={32} />
        </span>
        <h2>{BEFORE_START_COPY}</h2>
        {planForm}
      </section>
    );
  }

  return (
    <section className="panel">
      <DraftDisclosureBanner />

      <div className="section-heading" style={{ marginTop: 20 }}>
        <h2>Resumen del plan</h2>
      </div>
      <p style={{ whiteSpace: "pre-wrap" }}>{plan.summary}</p>
      {plan.experiment_id && (
        <p className="muted small-text">Operacionaliza el experimento {plan.experiment_id}.</p>
      )}
      {planForm}

      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Elementos del plan</h2>
      </div>
      {items.length === 0 ? (
        <p className="muted small-text">{EMPTY_ITEMS_COPY}</p>
      ) : (
        <div className="deliverables-grid">
          {items.map((item) => (
            <article className="panel deliverable-card" key={item.id}>
              <span className="deliverable-icon">
                <Icon name="calendar" size={19} />
              </span>
              <div>
                <h3>
                  #{item.sequence} · {item.format}
                </h3>
                <p className="small-text">{item.objective}</p>
                {item.scheduled_date && <p className="muted small-text">{item.scheduled_date}</p>}

                {item.brief ? (
                  <>
                    <p className="muted small-text" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>
                      Brief: {item.brief.brief}
                    </p>
                    {canProposePiece(role) && (
                      <div style={{ marginTop: 8 }}>
                        {pieceFormItemId !== item.id ? (
                          <button
                            type="button"
                            className="button"
                            onClick={() => {
                              setPieceFormItemId(item.id);
                              setPieceForm(EMPTY_PIECE_FORM);
                              setPieceError("");
                            }}
                          >
                            Crear pieza
                          </button>
                        ) : (
                          <div className="panel">
                            <div className="settings-field">
                              <label htmlFor={`piece-format-${item.id}`}>Formato</label>
                              <input
                                id={`piece-format-${item.id}`}
                                value={pieceForm.format}
                                disabled={piecePending}
                                onChange={(event) => setPieceForm((prev) => ({ ...prev, format: event.target.value }))}
                              />
                            </div>
                            <div className="settings-field">
                              <label htmlFor={`piece-objective-${item.id}`}>Objetivo</label>
                              <input
                                id={`piece-objective-${item.id}`}
                                value={pieceForm.objective}
                                disabled={piecePending}
                                onChange={(event) => setPieceForm((prev) => ({ ...prev, objective: event.target.value }))}
                              />
                            </div>
                            <div className="settings-field">
                              <label htmlFor={`piece-funnel-${item.id}`}>Etapa del embudo</label>
                              <input
                                id={`piece-funnel-${item.id}`}
                                value={pieceForm.funnelStage}
                                disabled={piecePending}
                                onChange={(event) => setPieceForm((prev) => ({ ...prev, funnelStage: event.target.value }))}
                              />
                            </div>
                            <div className="settings-field">
                              <label htmlFor={`piece-cta-${item.id}`}>Llamado a la acción</label>
                              <input
                                id={`piece-cta-${item.id}`}
                                value={pieceForm.cta}
                                disabled={piecePending}
                                onChange={(event) => setPieceForm((prev) => ({ ...prev, cta: event.target.value }))}
                              />
                            </div>
                            <div className="settings-field">
                              <label htmlFor={`piece-channel-${item.id}`}>Canal</label>
                              <input
                                id={`piece-channel-${item.id}`}
                                value={pieceForm.channel}
                                disabled={piecePending}
                                onChange={(event) => setPieceForm((prev) => ({ ...prev, channel: event.target.value }))}
                              />
                            </div>
                            <div className="settings-field">
                              <label htmlFor={`piece-payload-${item.id}`}>Contenido inicial (JSON, opcional)</label>
                              <textarea
                                id={`piece-payload-${item.id}`}
                                value={pieceForm.payload}
                                disabled={piecePending}
                                onChange={(event) => setPieceForm((prev) => ({ ...prev, payload: event.target.value }))}
                              />
                            </div>
                            <p className="muted small-text">
                              Esta pieza se registra para la campaña — no crea una versión aprobada, no
                              autoriza distribución ni ejecución de experimento.
                            </p>
                            <div className="settings-form-actions" style={{ marginTop: 8 }}>
                              <button
                                type="button"
                                className="button primary"
                                disabled={piecePending || !pieceFormIsFillable()}
                                onClick={() => submitPiece(item.id, item.brief!.id)}
                              >
                                Confirmar pieza
                              </button>
                              <button
                                type="button"
                                className="button"
                                disabled={piecePending}
                                onClick={() => {
                                  setPieceFormItemId(null);
                                  setPieceForm(EMPTY_PIECE_FORM);
                                }}
                              >
                                Cancelar
                              </button>
                            </div>
                            {pieceError && (
                              <p role="alert" className="settings-feedback">
                                {pieceError}
                              </p>
                            )}
                          </div>
                        )}
                        {pieceCreatedByItemId[item.id] && (
                          <p className="muted small-text" style={{ marginTop: 8 }}>
                            Pieza creada: {pieceCreatedByItemId[item.id]}. Consulta la vista de Contenido
                            para verla.
                          </p>
                        )}
                      </div>
                    )}
                  </>
                ) : (
                  canProposeBrief(role) && (
                    <div style={{ marginTop: 8 }}>
                      {briefFormItemId !== item.id ? (
                        <button
                          type="button"
                          className="button"
                          onClick={() => {
                            setBriefFormItemId(item.id);
                            setBriefText("");
                            setBriefError("");
                          }}
                        >
                          Redactar brief
                        </button>
                      ) : (
                        <div className="settings-field">
                          <label htmlFor={`brief-${item.id}`}>Brief para este elemento</label>
                          <textarea
                            id={`brief-${item.id}`}
                            value={briefText}
                            disabled={briefPending}
                            onChange={(event) => setBriefText(event.target.value)}
                          />
                          <div className="settings-form-actions" style={{ marginTop: 8 }}>
                            <button
                              type="button"
                              className="button primary"
                              disabled={briefPending || briefText.trim().length === 0}
                              onClick={() => submitBrief(item.id)}
                            >
                              Confirmar brief
                            </button>
                            <button
                              type="button"
                              className="button"
                              disabled={briefPending}
                              onClick={() => {
                                setBriefFormItemId(null);
                                setBriefText("");
                              }}
                            >
                              Cancelar
                            </button>
                          </div>
                          {briefError && (
                            <p role="alert" className="settings-feedback">
                              {briefError}
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  )
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
