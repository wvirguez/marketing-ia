"use client";

// MVP-07B: real Assets section for Content detail.
// MVP-16B: adds the write capability (create Creative Brief, create Asset
// with its atomic initial Version, append additional Versions) on top of
// the original read-only GET. Every mutation goes through the real,
// authenticated, CSRF-protected backend routes
// (apps/api/app/assets/router.py) — there is no optimistic/fake state
// anywhere in this file: every write replaces local state with the exact
// `AssetsForContentPieceResponse` the server returned, and a failed write
// leaves the last confirmed server state untouched.
//
// Hard invariants preserved throughout (apps/api/app/assets/models.py):
// CREATIVE BRIEF != ASSET. ASSET != ASSET VERSION. ASSET STATUS != CONTENT
// APPROVAL. ASSET EXISTS != READY FOR DISTRIBUTION/DISTRIBUTED. `status`
// is rendered as plain descriptive text, never as the colored `.status`
// pill used elsewhere for ContentPiece/Campaign governance state, and
// `storage_reference` is rendered as inert text, never as a clickable
// link, and never collected under a "URL" label — the backend supplies
// no URL, mime type, or file name, only an opaque external/storage
// reference string that may or may not be a URL (§12/§23 of the
// BACKEND-13 Governance Freeze; MVP-16A §Y).
//
// ASSET PRODUCTION GAP: resolved by MVP-16B for the create/append path —
// a real content piece can now obtain a Creative Brief, register Assets,
// and append Versions via the actions below. `status`/`metadata` remain
// unwritable from this form (MVP-16A §Z/§AA — no public mutation path for
// either exists yet); Archive remains unexposed (MVP-16A §V — deferred).
//
// Single-flight: at most one Assets mutation may be in flight at a time
// from this section — every write control is disabled while any one
// mutation is pending, mirroring TrackingPanel's own section-global lock.
// This is a same-section double-submit guard only; it does not and
// cannot protect against two separate tabs, devices, or API clients.

import { useEffect, useState, type FormEvent, type ReactElement } from "react";
import { Icon } from "@/components/ui/icon";
import { createAsset, createAssetVersion, createCreativeBrief, getAssetsForContent } from "@/lib/api/assets";
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
const REFERENCE_LABEL = "Referencia externa (opcional)";
const CREATE_BRIEF_LABEL = "Crear brief creativo";
const CREATE_ASSET_LABEL = "Registrar activo";
const ADD_VERSION_LABEL = "Añadir versión";
const KIND_MAX_LENGTH = 100;
const REFERENCE_MAX_LENGTH = 2048;

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

function AssetCard({
  asset,
  pending,
  onAppendVersion,
}: {
  asset: AssetPublic;
  pending: boolean;
  onAppendVersion: (assetId: string, storageReference: string | undefined) => void;
}) {
  const version = asset.current_version;
  const [referenceDraft, setReferenceDraft] = useState("");

  function submit() {
    const trimmed = referenceDraft.trim();
    onAppendVersion(asset.id, trimmed.length === 0 ? undefined : trimmed);
    setReferenceDraft("");
  }

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
              <dt className="muted small-text">{REFERENCE_LABEL}</dt>
              {/* Inert text only — never rendered as a link. The backend
                  supplies no URL; this string is an opaque reference that
                  may or may not be a URL. */}
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

        <div className="settings-fields" style={{ marginTop: 12 }}>
          <input
            type="text"
            value={referenceDraft}
            maxLength={REFERENCE_MAX_LENGTH}
            placeholder={REFERENCE_LABEL}
            disabled={pending}
            onChange={(event) => setReferenceDraft(event.target.value)}
            aria-label={`${REFERENCE_LABEL} para ${asset.kind}`}
          />
          <button type="button" className="button" disabled={pending} onClick={submit}>
            {ADD_VERSION_LABEL}
          </button>
        </div>
      </div>
    </article>
  );
}

function CreateCreativeBriefForm({ pending, onSubmit }: { pending: boolean; onSubmit: (summary: string) => void }) {
  const [draft, setDraft] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = draft.trim();
    if (trimmed.length === 0) return;
    onSubmit(trimmed);
  }

  return (
    <form className="panel settings-form" onSubmit={handleSubmit}>
      <div className="settings-field">
        <label htmlFor="creative-brief-summary">Resumen del brief</label>
        <input
          id="creative-brief-summary"
          type="text"
          value={draft}
          disabled={pending}
          onChange={(event) => setDraft(event.target.value)}
        />
      </div>
      <div className="settings-form-actions" style={{ marginTop: 8 }}>
        <button type="submit" className="button primary" disabled={pending || draft.trim().length === 0}>
          {CREATE_BRIEF_LABEL}
        </button>
      </div>
    </form>
  );
}

function CreateAssetForm({
  pending,
  onSubmit,
}: {
  pending: boolean;
  onSubmit: (kind: string, storageReference: string | undefined) => void;
}) {
  const [kindDraft, setKindDraft] = useState("");
  const [referenceDraft, setReferenceDraft] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const kind = kindDraft.trim();
    if (kind.length === 0) return;
    const reference = referenceDraft.trim();
    onSubmit(kind, reference.length === 0 ? undefined : reference);
    setKindDraft("");
    setReferenceDraft("");
  }

  return (
    <form className="panel settings-form" onSubmit={handleSubmit}>
      <div className="settings-fields">
        <div className="settings-field">
          <label htmlFor="asset-kind">Tipo de activo</label>
          <input
            id="asset-kind"
            type="text"
            value={kindDraft}
            maxLength={KIND_MAX_LENGTH}
            disabled={pending}
            onChange={(event) => setKindDraft(event.target.value)}
          />
        </div>
        <div className="settings-field">
          <label htmlFor="asset-reference">{REFERENCE_LABEL}</label>
          <input
            id="asset-reference"
            type="text"
            value={referenceDraft}
            maxLength={REFERENCE_MAX_LENGTH}
            disabled={pending}
            onChange={(event) => setReferenceDraft(event.target.value)}
          />
        </div>
      </div>
      <div className="settings-form-actions" style={{ marginTop: 8 }}>
        <button type="submit" className="button primary" disabled={pending || kindDraft.trim().length === 0}>
          {CREATE_ASSET_LABEL}
        </button>
      </div>
    </form>
  );
}

export function ContentAssetsSection({ campaignId, contentId }: { campaignId: string; contentId: string }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [pending, setPending] = useState(false);
  const [mutationError, setMutationError] = useState("");

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

  async function runMutation(action: () => Promise<AssetsForContentPieceResponse>) {
    if (pending) return;
    setPending(true);
    setMutationError("");
    try {
      const data = await action();
      setState({ status: "ready", data });
    } catch (error) {
      setMutationError(describeCampaignError(error));
    } finally {
      setPending(false);
    }
  }

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

        return (
          <>
            {creativeBrief === null ? (
              <section className="panel workspace-empty">
                <span className="workspace-empty-symbol">
                  <Icon name="image" size={32} />
                </span>
                <h3>{EMPTY_COPY}</h3>
                <p className="muted small-text">{EMPTY_SECONDARY_COPY}</p>
              </section>
            ) : (
              <section className="panel">
                <div className="section-heading">
                  <h3>Brief creativo</h3>
                </div>
                <JsonValue value={creativeBrief.spec} />
              </section>
            )}

            {creativeBrief === null && (
              <div style={{ marginTop: 16 }}>
                <CreateCreativeBriefForm
                  pending={pending}
                  onSubmit={(summary) => runMutation(() => createCreativeBrief(campaignId, contentId, { summary }))}
                />
              </div>
            )}

            {!fullyEmpty && (
              <>
                {assets.length === 0 ? (
                  <section className="panel" style={{ marginTop: 16 }}>
                    <p className="muted small-text">{NO_ASSETS_WITH_BRIEF_COPY}</p>
                  </section>
                ) : (
                  <div className="deliverables-grid" style={{ marginTop: 16 }}>
                    {assets.map((asset) => (
                      <AssetCard
                        key={asset.id}
                        asset={asset}
                        pending={pending}
                        onAppendVersion={(assetId, storageReference) =>
                          runMutation(() =>
                            createAssetVersion(campaignId, contentId, assetId, { storage_reference: storageReference }),
                          )
                        }
                      />
                    ))}
                  </div>
                )}
              </>
            )}

            {creativeBrief !== null && (
              <div style={{ marginTop: 16 }}>
                <CreateAssetForm
                  pending={pending}
                  onSubmit={(kind, storageReference) =>
                    runMutation(() => createAsset(campaignId, contentId, { kind, storage_reference: storageReference }))
                  }
                />
              </div>
            )}

            {mutationError && (
              <p role="alert" className="settings-feedback">
                {mutationError}
              </p>
            )}
          </>
        );
      })()}
    </>
  );
}
