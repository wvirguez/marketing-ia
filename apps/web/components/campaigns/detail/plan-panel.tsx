"use client";

// MVP-05C: real Plan tab. Same lazy-fetch/cache/invalidate lifecycle as
// ResearchPanel/AudiencePanel/StrategyPanel.
//
// READY FOR PLANNING != READY FOR PRODUCTION: PlanItemPublic carries no
// approval/readiness/production field on the backend, so none is
// fabricated here — no "Aprobado"/"Listo para publicar" badge exists in
// this panel.

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { getPlan } from "@/lib/api/planning";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { PlanOutputResponse } from "@/types/planning";
import { DraftDisclosureBanner } from "./draft-disclosure-banner";

type Result =
  | { token: number; status: "error"; message: string }
  | { token: number; status: "ready"; output: PlanOutputResponse };

const BEFORE_START_COPY = "Aún no se ha generado el borrador de planificación para esta campaña.";
const EMPTY_ITEMS_COPY = "Este borrador de planificación aún no contiene elementos.";

export function PlanPanel({
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
    getPlan(campaignId)
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

  if (!plan) {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="calendar" size={32} />
        </span>
        <h2>{BEFORE_START_COPY}</h2>
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
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
