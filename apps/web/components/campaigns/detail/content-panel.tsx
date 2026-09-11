"use client";

// MVP-05F: real Content tab. Same lazy-fetch/cache/invalidate lifecycle as
// ResearchPanel/AudiencePanel/StrategyPanel/PlanPanel — see research-panel.tsx's
// comment for the full reasoning behind `requestedTokenRef`.
//
// PRODUCED != APPROVED, READY FOR REVIEW != APPROVED FOR DISTRIBUTION,
// APPROVED FOR DISTRIBUTION != DISTRIBUTED (MVP-05E's own governance
// invariants) — this list shows the real backend status only, via
// contentPieceStatusLabel/Tone; it never renders an Approve/Reject/Submit
// for review/Publish control, since no public Content write endpoint
// exists to back one.

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { listContent } from "@/lib/api/content";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { contentPieceStatusLabel, contentPieceStatusTone, formatCampaignDate } from "@/lib/campaigns/status";
import type { ContentPieceListResponse } from "@/types/content";

type Result =
  | { token: number; status: "error"; message: string }
  | { token: number; status: "ready"; output: ContentPieceListResponse };

// Truthful, not a "no results found" search-style message — this is the
// normal, valid state both before Content has been generated and when a
// persisted Plan legitimately has zero items (MVP-05E §20/§AC).
const EMPTY_COPY = "Todavía no se ha generado contenido para esta campaña.";

export function ContentPanel({
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
    listContent(campaignId)
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
    listContent(campaignId)
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
          Cargando contenido…
        </p>
      </section>
    );
  }

  if (result.status === "error") {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="content" size={32} />
        </span>
        <h2>No pudimos cargar el contenido en este momento.</h2>
        <p>
          {result.message}{" "}
          <button type="button" className="auth-text-button" onClick={retry}>
            Reintentar
          </button>
        </p>
      </section>
    );
  }

  // Backend list ordering is authoritative for this phase — never sorted
  // client-side (MVP-05F §30).
  const { items } = result.output;

  if (items.length === 0) {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="content" size={32} />
        </span>
        <h2>{EMPTY_COPY}</h2>
      </section>
    );
  }

  return (
    <section className="panel">
      <div className="section-heading" style={{ marginTop: 4 }}>
        <h2>Contenido generado</h2>
      </div>
      <div className="deliverables-grid">
        {items.map((piece) => (
          <Link
            key={piece.id}
            href={`/campaigns/${campaignId}/content/${piece.id}`}
            className="panel deliverable-card"
            aria-label={`Ver contenido: ${piece.format} · ${piece.objective}`}
          >
            <span className="deliverable-icon">
              <Icon name="content" size={19} />
            </span>
            <div>
              <h3>{piece.format}</h3>
              <p className="small-text">{piece.objective}</p>
              <p className="muted small-text">
                {piece.channel} · {piece.funnel_stage}
              </p>
              <span className={`status ${contentPieceStatusTone(piece.status)}`}>
                <span />
                {contentPieceStatusLabel(piece.status)}
              </span>
              <p className="muted small-text">{formatCampaignDate(piece.created_at)}</p>
            </div>
          </Link>
        ))}
      </div>
    </section>
  );
}
