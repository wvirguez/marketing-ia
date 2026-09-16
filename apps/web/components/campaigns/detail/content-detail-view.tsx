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
// MVP-17B: adds the lifecycle + approval write controls (production
// workflow through READY_FOR_REVIEW, requesting/reviewing an Approval,
// and recording the final decision). Every mutation goes through the
// real, authenticated, CSRF-protected backend routes
// (apps/api/app/content/router.py) — no optimistic/fake state anywhere:
// every write replaces local state with the exact
// `ContentPieceDetailResponse` the server returned, and a failed write
// leaves the last confirmed server state untouched. FINAL CONTENT
// APPROVAL GOVERNANCE AUTHORITY = AGENT-00 (conceptual); this UI never
// claims AGENT-00 itself executed the decision — the wording below is
// deliberately "registrar decisión de aprobación" (a human recording a
// decision), never "aprobar para distribución" (MVP-17A §BC,
// MVP-17A-R1 §AB).
//
// MVP-20: adds the revision-loop control. Once a reviewer records
// CHANGES_REQUESTED, the piece moves to REVISION_REQUESTED and the ONLY
// action shown is "Crear versión revisada" — never "Marcar en
// producción" (that generic route is now DRAFT-only on the backend;
// see app/content/service.py::mark_in_production) and never "Solicitar
// aprobación" (the backend rejects it until a new version exists). This
// UI gating is reinforcement only — the actual guarantee is the backend
// command-level authority repair (MVP-20A-R1, CONTENT-P0-6).

import Link from "next/link";
import { useEffect, useState, type FormEvent, type ReactElement } from "react";
import { Icon } from "@/components/ui/icon";
import {
  associateTrackingRequirement,
  createContentRevisionVersion,
  dissociateTrackingRequirement,
  getContentDetail,
  markContentApprovalUnderReview,
  markContentInProduction,
  markContentProduced,
  markContentReadyForReview,
  markReadyForDistribution,
  recordDistributed,
  recordContentApprovalDecision,
  requestContentApproval,
} from "@/lib/api/content";
import { getTracking } from "@/lib/api/tracking";
import { useAuth } from "@/lib/auth/auth-context";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { contentPieceStatusLabel, contentPieceStatusTone, formatCampaignDate } from "@/lib/campaigns/status";
import type { ContentApprovalDecision, ContentApprovalStatus, ContentPieceDetailResponse, ContentVersionPublic } from "@/types/content";
import type { TrackingRequirementPublic } from "@/types/tracking";
import { ContentAssetsSection } from "./content-assets-section";
import { ContentDistributionEvidenceSection } from "./content-distribution-evidence-section";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; detail: ContentPieceDetailResponse };

const NO_VERSION_COPY = "Este contenido aún no tiene una versión generada.";

const APPROVAL_STATUS_LABELS: Record<string, string> = {
  REQUESTED: "Solicitada",
  UNDER_REVIEW: "En revisión",
  APPROVED: "Aprobada",
  CHANGES_REQUESTED: "Cambios solicitados",
  REJECTED: "Rechazada",
  EXPIRED: "Expirada",
};

const DECISION_LABELS: Record<ContentApprovalDecision, string> = {
  APPROVED: "Aprobar",
  CHANGES_REQUESTED: "Solicitar cambios",
  REJECTED: "Rechazar",
};

const DECISION_CONFIRM_COPY: Record<ContentApprovalDecision, string> = {
  APPROVED: "¿Confirmas que quieres aprobar este contenido?",
  CHANGES_REQUESTED: "",
  REJECTED: "¿Confirmas que quieres rechazar este contenido?",
};

// MVP-17A §BC / MVP-17A-R1 §AB: deliberately does not say "verificada" or
// imply automated quality/brand/evidence validation — the decision is a
// human, application-level record, never a claim of full governance
// validation.
const GOVERNANCE_NOTE =
  "Esta decisión la registra una persona autorizada de tu equipo; no verifica automáticamente calidad, evidencia ni cumplimiento de marca.";

// MVP-17A-R1 §I: an Approval is "open" while REQUESTED or UNDER_REVIEW —
// mirrors the backend's own definition exactly.
function isOpenApprovalStatus(status: ContentApprovalStatus): boolean {
  return status === "REQUESTED" || status === "UNDER_REVIEW";
}

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

// MVP-20: pre-fills from the current (about-to-be-superseded) Version's
// payload so the producer edits from a known starting point — the server
// never sees or trusts this pre-fill, only whatever the producer actually
// submits. V1 itself is never mutated; a genuinely new, immutable
// ContentVersion is what gets created.
function RevisionVersionForm({
  campaignId,
  contentId,
  latestVersion,
  pending,
  onMutate,
  onCancel,
}: {
  campaignId: string;
  contentId: string;
  latestVersion: ContentVersionPublic | null;
  pending: boolean;
  onMutate: (action: () => Promise<ContentPieceDetailResponse>) => void;
  onCancel: () => void;
}) {
  const [payloadText, setPayloadText] = useState(() => JSON.stringify(latestVersion?.payload ?? {}, null, 2));
  const [error, setError] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(payloadText);
    } catch {
      setError("El contenido debe ser JSON válido.");
      return;
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      setError("El contenido debe ser un objeto JSON.");
      return;
    }
    setError("");
    onMutate(() => createContentRevisionVersion(campaignId, contentId, { payload: parsed as Record<string, unknown> }));
  }

  return (
    <form className="panel" onSubmit={handleSubmit} noValidate style={{ marginTop: 16 }}>
      <div className="section-heading">
        <h4>Crear versión revisada</h4>
      </div>
      <p className="muted small-text">
        Edita el contenido a partir de la última versión. La versión anterior se conserva sin cambios; esto crea una
        versión nueva e independiente.
      </p>
      <label htmlFor="revision-version-payload">Contenido de la versión revisada</label>
      <br />
      <textarea
        id="revision-version-payload"
        rows={10}
        style={{ width: "100%", fontFamily: "monospace" }}
        value={payloadText}
        onChange={(event) => setPayloadText(event.target.value)}
      />
      {error && (
        <p className="small-text" role="alert">
          {error}
        </p>
      )}
      <div style={{ marginTop: 12, display: "flex", gap: 10 }}>
        <button type="submit" className="button primary" disabled={pending}>
          {pending ? "Guardando…" : "Crear versión revisada"}
        </button>
        <button type="button" className="auth-text-button" onClick={onCancel} disabled={pending}>
          Cancelar
        </button>
      </div>
    </form>
  );
}

function TrackingRequirementAssociationSection({
  campaignId,
  contentId,
  detail,
  trackingRequirements,
  pending,
  onMutate,
}: {
  campaignId: string;
  contentId: string;
  detail: ContentPieceDetailResponse;
  trackingRequirements: TrackingRequirementPublic[];
  pending: boolean;
  onMutate: (action: () => Promise<ContentPieceDetailResponse>) => void;
}) {
  const [selectedRequirementId, setSelectedRequirementId] = useState("");
  const distribution = detail.distribution;
  if (!distribution) return null;

  // MVP-24A-R1 §H: identity-only — names are resolved locally, purely for
  // display, from the independently-fetched CURRENT Tracking response;
  // never copied into or read from the Distribution response itself.
  const nameById = new Map(trackingRequirements.map((r) => [r.id, r.name]));
  const associatedIds = distribution.tracking_requirement_ids;
  const availableToAdd = trackingRequirements.filter((r) => !associatedIds.includes(r.id));
  const canMutateAssociation = distribution.status === "READY";

  return (
    <div style={{ marginTop: 16 }}>
      <div className="section-heading">
        <h4>Configuración de seguimiento asociada</h4>
      </div>
      <p className="muted small-text">
        Esta asociación identifica los requisitos de seguimiento registrados para esta distribución. No constituye
        atribución ni prueba de que el seguimiento haya producido las métricas reportadas.
      </p>

      {associatedIds.length === 0 ? (
        <p className="muted small-text">Aún no hay requisitos de seguimiento asociados a esta distribución.</p>
      ) : (
        <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
          {associatedIds.map((requirementId) => (
            <li key={requirementId} style={{ marginBottom: 4 }}>
              <span>{nameById.get(requirementId) ?? requirementId}</span>{" "}
              <span className="muted small-text">
                (estado actual del requisito: {trackingRequirements.find((r) => r.id === requirementId)?.status ?? "sin estado declarado"})
              </span>
              {canMutateAssociation && (
                <>
                  {" "}
                  <button
                    type="button"
                    className="auth-text-button"
                    disabled={pending}
                    onClick={() => onMutate(() => dissociateTrackingRequirement(campaignId, contentId, requirementId))}
                  >
                    Quitar asociación
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      {canMutateAssociation && availableToAdd.length > 0 && (
        <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <label htmlFor="tracking-requirement-select" className="muted small-text">
            Asociar requisito de seguimiento
          </label>
          <select
            id="tracking-requirement-select"
            value={selectedRequirementId}
            onChange={(event) => setSelectedRequirementId(event.target.value)}
            disabled={pending}
          >
            <option value="">Selecciona un requisito</option>
            {availableToAdd.map((requirement) => (
              <option key={requirement.id} value={requirement.id}>
                {requirement.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="button"
            disabled={pending || !selectedRequirementId}
            onClick={() => {
              if (!selectedRequirementId) return;
              onMutate(() => associateTrackingRequirement(campaignId, contentId, selectedRequirementId));
              setSelectedRequirementId("");
            }}
          >
            Asociar
          </button>
        </div>
      )}
    </div>
  );
}

function ContentLifecycleSection({
  campaignId,
  contentId,
  detail,
  role,
  pending,
  trackingRequirements,
  onMutate,
}: {
  campaignId: string;
  contentId: string;
  detail: ContentPieceDetailResponse;
  role: string | null;
  pending: boolean;
  trackingRequirements: TrackingRequirementPublic[];
  onMutate: (action: () => Promise<ContentPieceDetailResponse>) => void;
}) {
  const [armedDecision, setArmedDecision] = useState<ContentApprovalDecision | null>(null);
  const [distributionArmed, setDistributionArmed] = useState(false);
  const [externalReference, setExternalReference] = useState("");
  const [revisionFormOpen, setRevisionFormOpen] = useState(false);
  const { piece, latest_version: latestVersion, latest_approval: latestApproval } = detail;
  // MVP-17B §41: gated purely by the existing session's membership role
  // (no new permission concept) — MEMBER never sees these controls at
  // all, rather than seeing them disabled.
  const canDecide = role === "OWNER" || role === "ADMIN";

  function handleDecisionClick(decision: ContentApprovalDecision) {
    if (!latestApproval) return;
    // MVP-17A §BC/§42: confirmation required for APPROVED/REJECTED —
    // the first click arms it, a second click on the same action confirms.
    const requiresConfirmation = decision === "APPROVED" || decision === "REJECTED";
    if (requiresConfirmation && armedDecision !== decision) {
      setArmedDecision(decision);
      return;
    }
    setArmedDecision(null);
    onMutate(() => recordContentApprovalDecision(campaignId, contentId, latestApproval.id, decision));
  }

  return (
    <section className="panel" style={{ marginTop: 16 }}>
      <div className="section-heading">
        <h3>Flujo de producción y aprobación</h3>
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {piece.status === "DRAFT" && (
          <button
            type="button"
            className="button primary"
            disabled={pending}
            onClick={() => onMutate(() => markContentInProduction(campaignId, contentId))}
          >
            Marcar en producción
          </button>
        )}

        {/* MVP-20: REVISION_REQUESTED shows exactly one action — never
            "Marcar en producción" (backend-forbidden from this state) and
            never "Solicitar aprobación" (backend-rejected until a new
            Version exists). */}
        {piece.status === "REVISION_REQUESTED" && !revisionFormOpen && (
          <button type="button" className="button primary" disabled={pending} onClick={() => setRevisionFormOpen(true)}>
            Crear versión revisada
          </button>
        )}

        {piece.status === "IN_PRODUCTION" && (
          <button
            type="button"
            className="button primary"
            disabled={pending}
            onClick={() => onMutate(() => markContentProduced(campaignId, contentId))}
          >
            Marcar producido
          </button>
        )}

        {piece.status === "PRODUCED" && (
          <button
            type="button"
            className="button primary"
            disabled={pending}
            onClick={() => onMutate(() => markContentReadyForReview(campaignId, contentId))}
          >
            Marcar listo para revisión
          </button>
        )}

        {piece.status === "READY_FOR_REVIEW" && (!latestApproval || !isOpenApprovalStatus(latestApproval.status)) && (
          <button
            type="button"
            className="button primary"
            disabled={pending}
            onClick={() => onMutate(() => requestContentApproval(campaignId, contentId))}
          >
            Solicitar aprobación
          </button>
        )}

        {latestApproval && latestApproval.status === "REQUESTED" && (
          <button
            type="button"
            className="button primary"
            disabled={pending}
            onClick={() => onMutate(() => markContentApprovalUnderReview(campaignId, contentId, latestApproval.id))}
          >
            Marcar en revisión
          </button>
        )}
      </div>

      {piece.status === "REVISION_REQUESTED" && revisionFormOpen && (
        // No local onSaved/close-on-success wiring needed: once the
        // mutation succeeds, piece.status server-truth moves to
        // IN_PRODUCTION and this condition itself stops rendering the
        // form. On failure, piece.status is untouched, so the form (and
        // whatever the user had typed) stays exactly as it was — the
        // shared mutationError banner below reports the failure.
        <RevisionVersionForm
          campaignId={campaignId}
          contentId={contentId}
          latestVersion={latestVersion}
          pending={pending}
          onMutate={onMutate}
          onCancel={() => setRevisionFormOpen(false)}
        />
      )}

      {latestApproval && latestApproval.status === "UNDER_REVIEW" && canDecide && (
        <div style={{ marginTop: 16 }}>
          <div className="section-heading">
            <h4>Registrar decisión de aprobación</h4>
          </div>
          <p className="muted small-text">{GOVERNANCE_NOTE}</p>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 8 }}>
            {(Object.keys(DECISION_LABELS) as ContentApprovalDecision[]).map((decision) => (
              <button
                key={decision}
                type="button"
                className="button"
                disabled={pending}
                onClick={() => handleDecisionClick(decision)}
              >
                {armedDecision === decision ? "Confirmar" : DECISION_LABELS[decision]}
              </button>
            ))}
          </div>
          {armedDecision && (
            <p className="muted small-text" style={{ marginTop: 8 }}>
              {DECISION_CONFIRM_COPY[armedDecision]}{" "}
              <button type="button" className="auth-text-button" onClick={() => setArmedDecision(null)}>
                Cancelar
              </button>
            </p>
          )}
        </div>
      )}

      {latestApproval && (
        <p className="muted small-text" style={{ marginTop: 12 }}>
          Estado de la aprobación: {APPROVAL_STATUS_LABELS[latestApproval.status] ?? latestApproval.status}
        </p>
      )}
      {piece.status === "APPROVED" && role !== null && (
        <button type="button" className="button primary" disabled={pending}
          onClick={() => onMutate(() => markReadyForDistribution(campaignId, contentId))}>
          Marcar listo para distribución
        </button>
      )}
      {piece.status === "READY_FOR_DISTRIBUTION" && canDecide && (
        <div style={{ marginTop: 16 }}>
          <label htmlFor="distribution-external-reference">Referencia externa (opcional)</label>
          <input id="distribution-external-reference" type="text" maxLength={2048}
            value={externalReference} onChange={(event) => setExternalReference(event.target.value)} />
          <p className="muted small-text">Registra aquí una distribución que ya ocurrió fuera de esta aplicación. La aplicación no publica ni verifica el evento.</p>
          <button type="button" className="button primary" disabled={pending}
            onClick={() => {
              if (!distributionArmed) { setDistributionArmed(true); return; }
              setDistributionArmed(false);
              onMutate(() => recordDistributed(campaignId, contentId, externalReference || null));
            }}>
            {distributionArmed ? "Confirmar distribución realizada" : "Registrar distribución realizada"}
          </button>
          {distributionArmed && <button type="button" className="auth-text-button" onClick={() => setDistributionArmed(false)}>Cancelar</button>}
        </div>
      )}
      {detail.distribution?.status === "DISTRIBUTED" && (
        <p className="muted small-text">Distribución realizada registrada el {formatCampaignDate(detail.distribution.distributed_at ?? "")}.</p>
      )}
      <TrackingRequirementAssociationSection
        campaignId={campaignId}
        contentId={contentId}
        detail={detail}
        trackingRequirements={trackingRequirements}
        pending={pending}
        onMutate={onMutate}
      />
    </section>
  );
}

export function ContentDetailView({ campaignId, contentId }: { campaignId: string; contentId: string }) {
  const auth = useAuth();
  const role = auth.status === "authenticated" ? auth.session.membership.role : null;
  const [state, setState] = useState<State>({ status: "loading" });
  // MVP-17B §43: section-global single-flight for lifecycle/approval
  // mutations only — independent of ContentAssetsSection's own lock.
  const [pending, setPending] = useState(false);
  const [mutationError, setMutationError] = useState("");
  // MVP-24: best-effort, independent of the main content-detail fetch —
  // Tracking remains an optional domain; its own failure must never block
  // this page (mirrors DistributionEvidenceSection's own isolated-failure
  // convention). Refetched after every association mutation via
  // `onMutate`'s own response is NOT enough (that response only carries
  // Distribution-side ids) — a fresh GET /tracking keeps `status`/`name`
  // current-state-accurate independently.
  const [trackingRequirements, setTrackingRequirements] = useState<TrackingRequirementPublic[]>([]);

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

  // MVP-24: best-effort — a Tracking fetch failure (e.g. no TrackingPlan
  // recorded yet) never blocks or errors the Content detail page itself;
  // it simply leaves the association picker showing zero available
  // Requirements, which is already a truthful, safe empty state.
  function loadTrackingRequirements() {
    getTracking(campaignId)
      .then((response) => setTrackingRequirements(response.plan?.requirements ?? []))
      .catch(() => setTrackingRequirements([]));
  }

  useEffect(() => {
    loadTrackingRequirements();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaignId]);

  // AUTHORITATIVE SERVER TRUTH (MVP-17B §32/§44/§45): on success, local
  // state is replaced wholesale with the server's own response — never
  // optimistically patched. On failure, the last confirmed server state
  // is left untouched and the control remains retryable.
  async function runMutation(action: () => Promise<ContentPieceDetailResponse>) {
    if (pending) return;
    setPending(true);
    setMutationError("");
    try {
      const detail = await action();
      setState({ status: "ready", detail });
      // MVP-24: a Requirement's own current status can legitimately keep
      // changing independent of this Distribution (MVP-24A-R1 §K) — reload
      // it fresh after every mutation so the "estado actual" label shown
      // here never goes stale within this session.
      loadTrackingRequirements();
    } catch (error) {
      setMutationError(describeCampaignError(error));
    } finally {
      setPending(false);
    }
  }

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

      <ContentLifecycleSection
        campaignId={campaignId}
        contentId={contentId}
        detail={state.detail}
        role={role}
        pending={pending}
        trackingRequirements={trackingRequirements}
        onMutate={runMutation}
      />
      {mutationError && (
        <p role="alert" className="settings-feedback">
          {mutationError}
        </p>
      )}

      <ContentDistributionEvidenceSection
        campaignId={campaignId}
        contentId={contentId}
        distributed={piece.status === "DISTRIBUTED" && state.detail.distribution?.status === "DISTRIBUTED"}
      />

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

      <ContentAssetsSection campaignId={campaignId} contentId={contentId} />
    </div>
  );
}
