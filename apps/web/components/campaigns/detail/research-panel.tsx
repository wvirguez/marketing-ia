"use client";

// MVP-05B: real Research tab. Lazily fetches the first time this tab is
// activated (`active` becomes true), caches the result locally, and never
// refetches again on its own — only an actual campaign switch (this
// component is remounted with `key={campaignId}` by CampaignDetail) or a
// `refreshToken` bump (passed after a successful "Generar borrador" run)
// invalidates the cache and allows the next activation to fetch again.
//
// `requestedTokenRef` tracks which `refreshToken` this panel has already
// fetched (or is fetching) for — comparing it against the current
// `refreshToken` at render time, rather than storing a separate
// "loading" flag set synchronously inside the effect, keeps every
// `setState` call confined to the fetch's own async `.then`/`.catch`
// callbacks (react-hooks/set-state-in-effect).

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { getResearch } from "@/lib/api/research";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { ResearchOutputResponse } from "@/types/research";
import { DraftDisclosureBanner } from "./draft-disclosure-banner";

type Result =
  | { token: number; status: "error"; message: string }
  | { token: number; status: "ready"; output: ResearchOutputResponse };

const BEFORE_START_COPY = "Aún no se ha generado el borrador de investigación para esta campaña.";
const EMPTY_SOURCES_COPY = "No se incorporaron fuentes externas en este borrador inicial.";

export function ResearchPanel({
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

  // Manual retry (button click, not an effect) — no cancellation guard
  // needed for a one-off user-initiated action, matching
  // GenerateDraftAction's own `retryProgressLoad` precedent exactly.
  function retry() {
    requestedTokenRef.current = refreshToken;
    getResearch(campaignId)
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
    getResearch(campaignId)
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
          Cargando investigación…
        </p>
      </section>
    );
  }

  if (result.status === "error") {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="spark" size={32} />
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

  const { report, sources } = result.output;

  if (!report) {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="spark" size={32} />
        </span>
        <h2>{BEFORE_START_COPY}</h2>
      </section>
    );
  }

  return (
    <section className="panel">
      <DraftDisclosureBanner />

      <div className="section-heading" style={{ marginTop: 20 }}>
        <h2>Resumen de investigación</h2>
      </div>
      <p style={{ whiteSpace: "pre-wrap" }}>{report.summary}</p>

      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Fuentes externas</h2>
      </div>
      {sources.length === 0 ? (
        <p className="muted small-text">{EMPTY_SOURCES_COPY}</p>
      ) : (
        <div className="deliverables-grid">
          {sources.map((source) => (
            <article className="panel deliverable-card" key={source.id}>
              <span className="deliverable-icon">
                <Icon name="spark" size={19} />
              </span>
              <div>
                <h3>{source.title}</h3>
                <p className="muted small-text">
                  {source.source_type}
                  {source.publisher ? ` · ${source.publisher}` : ""}
                </p>
                {source.excerpt && <p className="small-text">{source.excerpt}</p>}
                {source.locator && <p className="muted small-text">{source.locator}</p>}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
