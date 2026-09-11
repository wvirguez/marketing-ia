"use client";

// MVP-05F: real Content detail view. Fetches
// GET /campaigns/{campaignId}/content/{contentId} directly — works on a
// direct browser reload, with no dependency on having first opened the
// campaign's Content tab.
//
// Hard invariant preserved throughout: CONTENT PIECE != CONTENT VERSION.
// The Piece's own business fields (identity/status) and the latest
// Version's payload are rendered as two visually distinct sections, never
// merged into one object. `ContentVersion.payload` is untyped JSON — this
// view never assumes a Reel/Carousel/Story shape (hook/scenes/slides/
// hashtags); it renders whatever keys/values are actually present via a
// generic, safe key/value renderer (no dangerouslySetInnerHTML, ever).
//
// No approval/production/distribution control is rendered anywhere here:
// no public Content write endpoint exists to back one (MVP-05E §14/§15).

import Link from "next/link";
import { useEffect, useState, type ReactElement } from "react";
import { Icon } from "@/components/ui/icon";
import { getContentDetail } from "@/lib/api/content";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { contentPieceStatusLabel, contentPieceStatusTone, formatCampaignDate } from "@/lib/campaigns/status";
import type { ContentPieceDetailResponse } from "@/types/content";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; detail: ContentPieceDetailResponse };

const NO_VERSION_COPY = "Este contenido aún no tiene una versión generada.";

function JsonValue({ value }: { value: unknown }): ReactElement {
  if (value === null || value === undefined || value === "") {
    return <span className="muted small-text">—</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="muted small-text">—</span>;
    return (
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {value.map((item, index) => (
          <li key={index} style={{ marginBottom: 6 }}>
            <JsonValue value={item} />
          </li>
        ))}
      </ul>
    );
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span className="muted small-text">—</span>;
    return (
      <dl style={{ margin: 0, paddingLeft: 12, borderLeft: "2px solid var(--border)" }}>
        {entries.map(([key, val]) => (
          <div key={key} style={{ marginBottom: 8 }}>
            <dt className="muted small-text">{key}</dt>
            <dd style={{ margin: 0 }}>
              <JsonValue value={val} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  // Scalar (string/number/boolean). Text content only — never rendered as
  // markup, so a payload string cannot execute or inject HTML.
  return <span style={{ whiteSpace: "pre-wrap" }}>{String(value)}</span>;
}

export function ContentDetailView({ campaignId, contentId }: { campaignId: string; contentId: string }) {
  const [state, setState] = useState<State>({ status: "loading" });

  // Manual retry (button click, not an effect) — no cancellation guard
  // needed for a one-off user-initiated action, matching
  // GenerateDraftAction's own `retryProgressLoad` precedent.
  function retry() {
    setState({ status: "loading" });
    getContentDetail(campaignId, contentId)
      .then((detail) => setState({ status: "ready", detail }))
      .catch((error) => setState({ status: "error", message: describeCampaignError(error) }));
  }

  useEffect(() => {
    let cancelled = false;
    getContentDetail(campaignId, contentId)
      .then((detail) => {
        // Guards a late response for a previous contentId from ever
        // rendering under a newer campaignId/contentId identity.
        if (!cancelled) setState({ status: "ready", detail });
      })
      .catch((error) => {
        if (!cancelled) setState({ status: "error", message: describeCampaignError(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, contentId]);

  const backLink = (
    <Link href={`/campaigns/${campaignId}`} className="button secondary">
      <Icon name="arrow" size={15} /> Volver a la campaña
    </Link>
  );

  if (state.status === "loading") {
    return (
      <div className="dashboard campaign-workspace">
        <p className="muted small-text" role="status">
          Cargando contenido…
        </p>
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="dashboard campaign-workspace">
        <section className="panel workspace-empty">
          <span className="workspace-empty-symbol">
            <Icon name="content" size={32} />
          </span>
          <h2>No pudimos abrir este contenido.</h2>
          <p>{state.message}</p>
          <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
            <button type="button" className="auth-text-button" onClick={retry}>
              Reintentar
            </button>
            {backLink}
          </div>
        </section>
      </div>
    );
  }

  const { piece, latest_version: latestVersion } = state.detail;

  return (
    <div className="dashboard campaign-workspace">
      <div style={{ marginBottom: 16 }}>{backLink}</div>

      <header className="workspace-header">
        <div className="workspace-title-row">
          <span className="workspace-campaign-icon">
            <Icon name="content" size={27} />
          </span>
          <div>
            <div className="eyebrow">
              <span /> CONTENIDO
            </div>
            <h1>{piece.format}</h1>
          </div>
          <span className={`status ${contentPieceStatusTone(piece.status)}`}>
            <span />
            {contentPieceStatusLabel(piece.status)}
          </span>
        </div>
        <dl className="workspace-metadata">
          <div>
            <dt>Objetivo</dt>
            <dd>{piece.objective}</dd>
          </div>
          <div>
            <dt>Canal</dt>
            <dd>{piece.channel}</dd>
          </div>
          <div>
            <dt>Etapa de embudo</dt>
            <dd>{piece.funnel_stage}</dd>
          </div>
          <div>
            <dt>CTA</dt>
            <dd>{piece.cta}</dd>
          </div>
          <div>
            <dt>Creado</dt>
            <dd>{formatCampaignDate(piece.created_at)}</dd>
          </div>
          {piece.archived_at && (
            <div>
              <dt>Archivado</dt>
              <dd>{formatCampaignDate(piece.archived_at)}</dd>
            </div>
          )}
        </dl>
      </header>

      <section className="panel">
        <p className="muted small-text" role="note">
          <Icon name="spark" size={14} /> Este es un borrador inicial derivado de los datos de la campaña y del
          contexto de planificación ya generado. No incluye investigación externa ni validación independiente, y no
          está aprobado para distribución.
        </p>
      </section>

      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Última versión</h2>
      </div>
      {latestVersion ? (
        <section className="panel">
          <p className="muted small-text" style={{ marginBottom: 14 }}>
            Generada el {formatCampaignDate(latestVersion.created_at)}
          </p>
          <JsonValue value={latestVersion.payload} />
        </section>
      ) : (
        <section className="panel workspace-empty">
          <span className="workspace-empty-symbol">
            <Icon name="content" size={32} />
          </span>
          <h2>{NO_VERSION_COPY}</h2>
        </section>
      )}
    </div>
  );
}
