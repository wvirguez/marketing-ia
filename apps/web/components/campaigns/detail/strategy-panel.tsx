"use client";

// MVP-05C: real Strategy tab. Same lazy-fetch/cache/invalidate lifecycle
// as ResearchPanel/AudiencePanel — see research-panel.tsx's comment for
// the full reasoning behind `requestedTokenRef`.

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { useAuth } from "@/lib/auth/auth-context";
import { createExperiment, createHypothesis, getStrategy } from "@/lib/api/strategy";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { HypothesisStatus, StrategyOutputResponse } from "@/types/strategy";
import { DraftDisclosureBanner } from "./draft-disclosure-banner";
import { StrategyRevisionSection } from "./strategy-revision-section";

type Result =
  | { token: number; status: "error"; message: string }
  | { token: number; status: "ready"; output: StrategyOutputResponse };

const BEFORE_START_COPY = "Aún no se ha generado el borrador de estrategia para esta campaña.";
const NO_POSITIONING_COPY = "Este borrador aún no incluye una propuesta de posicionamiento.";
const EMPTY_HYPOTHESES_COPY = "Este borrador aún no contiene hipótesis estratégicas.";
const EMPTY_EXPERIMENTS_COPY = "No se han definido experimentos en este borrador inicial.";

// Truthful, non-inflating labels for the backend's own status vocabulary
// (BACKEND-08: open/confirmed/refuted) — never "hallazgo", "conclusión",
// or "insight validado". OPEN is the only status the deterministic
// bootstrap currently produces; CONFIRMED/REFUTED are supported here only
// because the schema allows them, not because this phase's bootstrap
// generates them.
const HYPOTHESIS_STATUS_LABELS: Record<HypothesisStatus, string> = {
  OPEN: "Hipótesis por validar",
  CONFIRMED: "Hipótesis confirmada",
  REFUTED: "Hipótesis descartada",
};

// MVP-31A §T: any active workspace membership (MEMBER+) may propose a
// governed Hypothesis — a contributory/analytical action, never one that
// commits or mutates the authoritative Strategy/Positioning state — the
// same authority tier already established for StrategicImplication/
// StrategicRecommendation creation (learning-panel.tsx's own precedent).
function canProposeHypothesis(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN" || role === "MEMBER";
}

// MVP-32A §R: same MEMBER+ authority tier as Hypothesis creation — an
// Experiment proposes, it never commits or mutates authoritative state.
function canProposeExperiment(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN" || role === "MEMBER";
}

export function StrategyPanel({
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
  const [showHypothesisForm, setShowHypothesisForm] = useState(false);
  const [hypothesisStatement, setHypothesisStatement] = useState("");
  const [hypothesisPending, setHypothesisPending] = useState(false);
  const [hypothesisError, setHypothesisError] = useState("");
  const [activeExperimentHypothesisId, setActiveExperimentHypothesisId] = useState<string | null>(null);
  const [experimentDescription, setExperimentDescription] = useState("");
  const [experimentPending, setExperimentPending] = useState(false);
  const [experimentError, setExperimentError] = useState("");

  function retry() {
    requestedTokenRef.current = refreshToken;
    getStrategy(campaignId)
      .then((output) => setResult({ token: refreshToken, status: "ready", output }))
      .catch((error) => {
        requestedTokenRef.current = null;
        setResult({ token: refreshToken, status: "error", message: describeCampaignError(error) });
      });
  }

  useEffect(() => {
    if (!active || requestedTokenRef.current === refreshToken) return;
    let cancelled = false;
    requestedTokenRef.current = refreshToken;
    getStrategy(campaignId)
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

  const loading = result === null || result.token !== refreshToken;

  if (loading) {
    return (
      <section className="panel">
        <p className="muted small-text" role="status">
          Cargando estrategia…
        </p>
      </section>
    );
  }

  if (result.status === "error") {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="target" size={32} />
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

  const { strategy, positioning, hypotheses, experiments } = result.output;

  if (!strategy) {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="target" size={32} />
        </span>
        <h2>{BEFORE_START_COPY}</h2>
      </section>
    );
  }

  async function submitHypothesis() {
    if (hypothesisPending || hypothesisStatement.trim().length === 0 || !strategy) return;
    setHypothesisPending(true);
    setHypothesisError("");
    try {
      await createHypothesis(campaignId, strategy.id, hypothesisStatement.trim());
      setHypothesisStatement("");
      setShowHypothesisForm(false);
      retry();
    } catch (error) {
      setHypothesisError(describeCampaignError(error));
    } finally {
      setHypothesisPending(false);
    }
  }

  async function submitExperiment(hypothesisId: string) {
    if (experimentPending || experimentDescription.trim().length === 0) return;
    setExperimentPending(true);
    setExperimentError("");
    try {
      await createExperiment(campaignId, hypothesisId, experimentDescription.trim());
      setExperimentDescription("");
      setActiveExperimentHypothesisId(null);
      retry();
    } catch (error) {
      setExperimentError(describeCampaignError(error));
    } finally {
      setExperimentPending(false);
    }
  }

  return (
    <section className="panel">
      <DraftDisclosureBanner />

      <div className="section-heading" style={{ marginTop: 20 }}>
        <h2>Resumen de estrategia</h2>
      </div>
      <p style={{ whiteSpace: "pre-wrap" }}>{strategy.summary}</p>

      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Posicionamiento</h2>
      </div>
      {positioning ? (
        <p style={{ whiteSpace: "pre-wrap" }}>{positioning.statement}</p>
      ) : (
        <p className="muted small-text">{NO_POSITIONING_COPY}</p>
      )}

      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Hipótesis</h2>
      </div>
      {hypotheses.length === 0 ? (
        <p className="muted small-text">{EMPTY_HYPOTHESES_COPY}</p>
      ) : (
        <div className="deliverables-grid">
          {hypotheses.map((hypothesis) => {
            const hypothesisExperiments = experiments.filter((experiment) => experiment.hypothesis_id === hypothesis.id);
            return (
              <article className="panel deliverable-card" key={hypothesis.id}>
                <span className="deliverable-icon">
                  <Icon name="target" size={19} />
                </span>
                <div>
                  <p>{hypothesis.statement}</p>
                  <span className="status draft">
                    <span />
                    {HYPOTHESIS_STATUS_LABELS[hypothesis.status] ?? hypothesis.status}
                  </span>

                  {hypothesisExperiments.length === 0 ? (
                    <p className="muted small-text" style={{ marginTop: 8 }}>
                      {EMPTY_EXPERIMENTS_COPY}
                    </p>
                  ) : (
                    <ul style={{ marginTop: 8 }}>
                      {hypothesisExperiments.map((experiment) => (
                        <li key={experiment.id}>
                          <p>{experiment.description}</p>
                          <p className="muted small-text">{experiment.status ?? "Sin estado registrado"}</p>
                        </li>
                      ))}
                    </ul>
                  )}

                  {canProposeExperiment(role) && activeExperimentHypothesisId !== hypothesis.id && (
                    <div className="settings-form-actions" style={{ marginTop: 8 }}>
                      <button
                        type="button"
                        className="button"
                        onClick={() => {
                          setActiveExperimentHypothesisId(hypothesis.id);
                          setExperimentDescription("");
                          setExperimentError("");
                        }}
                      >
                        Proponer experimento
                      </button>
                    </div>
                  )}
                  {activeExperimentHypothesisId === hypothesis.id && (
                    <div className="panel" style={{ marginTop: 8 }}>
                      <div className="settings-field">
                        <label htmlFor={`experiment-description-${hypothesis.id}`}>Nuevo experimento</label>
                        <textarea
                          id={`experiment-description-${hypothesis.id}`}
                          value={experimentDescription}
                          disabled={experimentPending}
                          onChange={(event) => setExperimentDescription(event.target.value)}
                        />
                      </div>
                      <p className="muted small-text">
                        Este experimento se registra bajo esta hipótesis — no crea planes de contenido ni ningún
                        otro contenido, y no autoriza ejecución externa.
                      </p>
                      <div className="settings-form-actions" style={{ marginTop: 8 }}>
                        <button
                          type="button"
                          className="button primary"
                          disabled={experimentPending || experimentDescription.trim().length === 0}
                          onClick={() => submitExperiment(hypothesis.id)}
                        >
                          Confirmar experimento
                        </button>
                        <button
                          type="button"
                          className="button"
                          disabled={experimentPending}
                          onClick={() => {
                            setActiveExperimentHypothesisId(null);
                            setExperimentDescription("");
                          }}
                        >
                          Cancelar
                        </button>
                      </div>
                      {experimentError && (
                        <p role="alert" className="settings-feedback">
                          {experimentError}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}

      {canProposeHypothesis(role) && !showHypothesisForm && (
        <div className="settings-form-actions" style={{ marginTop: 8 }}>
          <button type="button" className="button" onClick={() => setShowHypothesisForm(true)}>
            Proponer hipótesis
          </button>
        </div>
      )}
      {showHypothesisForm && (
        <div className="panel" style={{ marginTop: 8 }}>
          <div className="settings-field">
            <label htmlFor="hypothesis-statement">Nueva hipótesis</label>
            <textarea
              id="hypothesis-statement"
              value={hypothesisStatement}
              disabled={hypothesisPending}
              onChange={(event) => setHypothesisStatement(event.target.value)}
            />
          </div>
          <p className="muted small-text">
            Esta hipótesis se registra bajo la versión vigente de la estrategia — no crea experimentos ni ningún
            otro contenido, y no autoriza ejecución externa.
          </p>
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="button primary"
              disabled={hypothesisPending || hypothesisStatement.trim().length === 0}
              onClick={submitHypothesis}
            >
              Confirmar hipótesis
            </button>
            <button
              type="button"
              className="button"
              disabled={hypothesisPending}
              onClick={() => {
                setShowHypothesisForm(false);
                setHypothesisStatement("");
              }}
            >
              Cancelar
            </button>
          </div>
          {hypothesisError && (
            <p role="alert" className="settings-feedback">
              {hypothesisError}
            </p>
          )}
        </div>
      )}

      <StrategyRevisionSection campaignId={campaignId} currentStrategyId={strategy.id} role={role} onRevised={retry} />
    </section>
  );
}
