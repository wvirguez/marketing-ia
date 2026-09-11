"use client";

// MVP-05B: real Audience tab. Same lazy-fetch/cache/invalidate lifecycle
// as ResearchPanel — see its comment for the full reasoning.

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { getAudience } from "@/lib/api/research";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { AudienceOutputResponse } from "@/types/research";
import { DraftDisclosureBanner } from "./draft-disclosure-banner";

type Result =
  | { token: number; status: "error"; message: string }
  | { token: number; status: "ready"; output: AudienceOutputResponse };

const BEFORE_START_COPY = "Aún no se ha generado el borrador de audiencia para esta campaña.";
const EMPTY_VOC_COPY = "No se ha incorporado evidencia de voz del cliente (VOC) externa en este borrador.";

export function AudiencePanel({
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
    getAudience(campaignId)
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
    getAudience(campaignId)
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
          Cargando audiencia…
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

  const { profile, voc_evidence: vocEvidence } = result.output;

  if (!profile) {
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
        <h2>Perfil de audiencia</h2>
      </div>
      <p style={{ whiteSpace: "pre-wrap" }}>{profile.summary}</p>

      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Voz del cliente (VOC)</h2>
      </div>
      {vocEvidence.length === 0 ? (
        <p className="muted small-text">{EMPTY_VOC_COPY}</p>
      ) : (
        <div className="deliverables-grid">
          {vocEvidence.map((item) => (
            <article className="panel deliverable-card" key={item.id}>
              <span className="deliverable-icon">
                <Icon name="target" size={19} />
              </span>
              <div>
                {item.verbatim_quote && <p>&ldquo;{item.verbatim_quote}&rdquo;</p>}
                {item.paraphrase && <p className="small-text">{item.paraphrase}</p>}
                <p className="muted small-text">
                  {item.source_type ?? "Origen no especificado"}
                  {item.source_locator ? ` · ${item.source_locator}` : ""}
                </p>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
