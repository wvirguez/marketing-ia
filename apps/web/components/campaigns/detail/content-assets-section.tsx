"use client";

// MVP-07B: real, read-only Assets section for Content detail. Fetches
// GET /campaigns/{campaignId}/content/{contentId}/assets directly — the
// only Assets endpoint that exists (apps/api/app/assets/router.py); no
// write capability of any kind is exposed by the backend, so this
// component never mutates, generates, uploads, or archives anything.
//
// Hard invariants preserved throughout (apps/api/app/assets/models.py):
// CREATIVE BRIEF != ASSET. ASSET != ASSET VERSION. ASSET STATUS != CONTENT
// APPROVAL. ASSET EXISTS != READY FOR DISTRIBUTION/DISTRIBUTED. `status`
// is rendered as plain descriptive text, never as the colored `.status`
// pill used elsewhere for ContentPiece/Campaign governance state, and
// `storage_reference` is rendered as inert text, never as a clickable
// link — the backend supplies no URL, mime type, or file name, only an
// opaque reference string (§19/§23 of the BACKEND-13 Governance Freeze).
//
// ASSET PRODUCTION GAP (preserved, not solved here): no production caller
// currently creates CreativeBrief/Asset/AssetVersion rows, so every real
// campaign today returns `{ creative_brief: null, assets: [] }` — the
// empty state below is written to be truthful about that, not to imply
// generation is pending, in progress, or failed.

import { useEffect, useState, type ReactElement } from "react";
import { Icon } from "@/components/ui/icon";
import { getAssetsForContent } from "@/lib/api/assets";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { formatCampaignDate } from "@/lib/campaigns/status";
import type { AssetPublic, AssetsForContentPieceResponse } from "@/types/assets";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: AssetsForContentPieceResponse };

const EMPTY_COPY = "No hay creatividades registradas para esta pieza todavía.";
const EMPTY_SECONDARY_COPY =
  "Esta sección muestra las creatividades y sus activos persistidos cuando existen.";
const NO_ASSETS_WITH_BRIEF_COPY = "No hay activos registrados para este brief todavía.";
const NO_VERSION_COPY = "Este activo no tiene una versión registrada disponible.";

// Local, minimal recursive renderer for opaque JSON (CreativeBrief.spec,
// AssetVersion.metadata) — mirrors ContentDetailView's own JsonValue
// exactly (same shape, same "never dangerouslySetInnerHTML" discipline),
// duplicated locally since that renderer is not exported.
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
  return <span style={{ whiteSpace: "pre-wrap" }}>{String(value)}</span>;
}

function AssetCard({ asset }: { asset: AssetPublic }) {
  const version = asset.current_version;
  return (
    <article className="panel deliverable-card">
      <span className="deliverable-icon">
        <Icon name="image" size={19} />
      </span>
      <div>
        <dl style={{ margin: 0 }}>
          <div>
            <dt className="muted small-text">Tipo</dt>
            <dd style={{ margin: 0 }}>{asset.kind}</dd>
          </div>
          {asset.status !== null && (
            <div style={{ marginTop: 8 }}>
              <dt className="muted small-text">Estado</dt>
              {/* Plain descriptive text only — never the colored `.status`
                  pill used for ContentPiece/Campaign governance state
                  (Asset.status has no state machine and no approval
                  authority). */}
              <dd style={{ margin: 0 }}>{asset.status}</dd>
            </div>
          )}
        </dl>

        <div className="section-heading" style={{ marginTop: 14 }}>
          <h4>Versión actual</h4>
        </div>
        {version ? (
          <dl style={{ margin: 0 }}>
            <div>
              <dt className="muted small-text">Creada</dt>
              <dd style={{ margin: 0 }}>{formatCampaignDate(version.created_at)}</dd>
            </div>
            <div style={{ marginTop: 8 }}>
              <dt className="muted small-text">Referencia de almacenamiento</dt>
              {/* Inert text only — never rendered as a link. The backend
                  supplies no URL, so this string cannot be assumed to be
                  reachable or downloadable. */}
              <dd style={{ margin: 0 }}>{version.storage_reference ?? <span className="muted small-text">—</span>}</dd>
            </div>
            <div style={{ marginTop: 8 }}>
              <dt className="muted small-text">Metadata</dt>
              <dd style={{ margin: 0 }}>
                <JsonValue value={version.metadata} />
              </dd>
            </div>
          </dl>
        ) : (
          <p className="muted small-text">{NO_VERSION_COPY}</p>
        )}
      </div>
    </article>
  );
}

export function ContentAssetsSection({ campaignId, contentId }: { campaignId: string; contentId: string }) {
  const [state, setState] = useState<State>({ status: "loading" });

  // Manual retry (button click, not an effect) — no cancellation guard
  // needed for a one-off user-initiated action, matching
  // ContentDetailView's own `retry` precedent exactly.
  function retry() {
    setState({ status: "loading" });
    getAssetsForContent(campaignId, contentId)
      .then((data) => setState({ status: "ready", data }))
      .catch((error) => setState({ status: "error", message: describeCampaignError(error) }));
  }

  useEffect(() => {
    let cancelled = false;
    getAssetsForContent(campaignId, contentId)
      .then((data) => {
        if (!cancelled) setState({ status: "ready", data });
      })
      .catch((error) => {
        if (!cancelled) setState({ status: "error", message: describeCampaignError(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, contentId]);

  return (
    <>
      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Creatividades</h2>
      </div>

      {state.status === "loading" && (
        <section className="panel">
          <p className="muted small-text" role="status">
            Cargando creatividades…
          </p>
        </section>
      )}

      {state.status === "error" && (
        <section className="panel workspace-empty">
          <span className="workspace-empty-symbol">
            <Icon name="image" size={32} />
          </span>
          <h3>No pudimos cargar las creatividades en este momento.</h3>
          <p>
            {state.message}{" "}
            <button type="button" className="auth-text-button" onClick={retry}>
              Reintentar
            </button>
          </p>
        </section>
      )}

      {state.status === "ready" && (() => {
        const { creative_brief: creativeBrief, assets } = state.data;
        const fullyEmpty = creativeBrief === null && assets.length === 0;

        if (fullyEmpty) {
          return (
            <section className="panel workspace-empty">
              <span className="workspace-empty-symbol">
                <Icon name="image" size={32} />
              </span>
              <h3>{EMPTY_COPY}</h3>
              <p className="muted small-text">{EMPTY_SECONDARY_COPY}</p>
            </section>
          );
        }

        return (
          <>
            {creativeBrief !== null && (
              <section className="panel">
                <div className="section-heading">
                  <h3>Brief creativo</h3>
                </div>
                <JsonValue value={creativeBrief.spec} />
              </section>
            )}

            {assets.length === 0 ? (
              <section className="panel">
                <p className="muted small-text">{NO_ASSETS_WITH_BRIEF_COPY}</p>
              </section>
            ) : (
              <div className="deliverables-grid">
                {assets.map((asset) => (
                  <AssetCard key={asset.id} asset={asset} />
                ))}
              </div>
            )}
          </>
        );
      })()}
    </>
  );
}
