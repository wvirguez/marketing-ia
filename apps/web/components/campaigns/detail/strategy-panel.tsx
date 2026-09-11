"use client";

// MVP-05C: real Strategy tab. Same lazy-fetch/cache/invalidate lifecycle
// as ResearchPanel/AudiencePanel — see research-panel.tsx's comment for
// the full reasoning behind `requestedTokenRef`.

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { getStrategy } from "@/lib/api/strategy";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { HypothesisStatus, StrategyOutputResponse } from "@/types/strategy";
import { DraftDisclosureBanner } from "./draft-disclosure-banner";

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

export function StrategyPanel({
  campaignId,
  active,
  refreshToken,
}: {
  campaignId: string;
  active: boolean;
  refreshToken: number;
}) {
  const [result, setResult] = useState<Result | null>(null);
  const requestedTokenRef = useRef<number | null>(null);

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
          {hypotheses.map((hypothesis) => (
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
              </div>
            </article>
          ))}
        </div>
      )}

      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Experimentos</h2>
      </div>
      {experiments.length === 0 ? (
        <p className="muted small-text">{EMPTY_EXPERIMENTS_COPY}</p>
      ) : (
        <div className="deliverables-grid">
          {experiments.map((experiment) => (
            <article className="panel deliverable-card" key={experiment.id}>
              <span className="deliverable-icon">
                <Icon name="target" size={19} />
              </span>
              <div>
                <p>{experiment.description}</p>
                <p className="muted small-text">{experiment.status ?? "Sin estado registrado"}</p>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
