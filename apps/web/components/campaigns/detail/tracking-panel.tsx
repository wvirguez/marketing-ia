"use client";

// MVP-08B: real Tracking panel for the campaign "Tracking" tab.
// MVP-15B: adds the write capability (create Plan, create Requirement,
// Plan transitions, Requirement status updates) on top of the original
// read-only GET. Every mutation goes through the real, authenticated,
// CSRF-protected backend routes (apps/api/app/tracking/router.py) — there
// is no optimistic/fake state anywhere in this file: every write replaces
// local state with the exact `TrackingResponse` the server returned, and a
// failed write leaves the last confirmed server state untouched.
//
// Hard invariants preserved throughout (apps/api/app/tracking/models.py,
// apps/api/app/tracking/service.py): TRACKING PLAN != TRACKING
// REQUIREMENT. TRACKING STATUS != TECHNICAL VERIFICATION. CERTIFIED !=
// SYSTEM-VERIFIED. CERTIFIED != MEASUREMENT RESULT. `plan.status` is a
// manually-updated, self-reported workflow marker — the backend's own
// service docstring states "CERTIFIED is a manual, self-declared
// attestation only" with zero automated/network verification behind it.
// This panel renders status as plain descriptive metadata under "Estado
// declarado," never as a colored success/approval badge, and requires an
// explicit confirmation step (repeating the same disclaimer) before
// submitting a transition into CERTIFIED specifically.
//
// TRACKING PRODUCTION GAP: resolved by MVP-15B for the create path — a
// real campaign can now obtain a Plan via the "Crear plan de tracking
// manual" action below. Requirement authoring is manual/user-typed only;
// no system-generated default requirements exist anywhere in the backend
// domain, so none are invented here.
//
// Single-flight: at most one Tracking mutation may be in flight at a time
// from this panel — every write control is disabled while any one
// mutation is pending, mirroring NotificationsSection's own
// `submittingKey`-style guard. This is a same-panel double-submit guard
// only; it does not and cannot protect against two separate tabs,
// devices, or API clients racing the same backend row — that remains a
// backend-only concern (unaffected by this panel).

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { createTrackingPlan, createTrackingRequirement, getTracking, patchTracking } from "@/lib/api/tracking";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { TrackingPlanPublic, TrackingReadinessStatus, TrackingRequirementPublic, TrackingResponse } from "@/types/tracking";

type Result =
  | { token: number; status: "error"; message: string }
  | { token: number; status: "ready"; plan: TrackingPlanPublic | null };

const EMPTY_COPY = "Aún no se ha definido un plan de tracking para esta campaña.";
const EMPTY_SECONDARY_COPY =
  "Cuando exista un plan, aquí podrás consultar sus requisitos y el estado declarado.";
const CREATE_PLAN_LABEL = "Crear plan de tracking manual";
const ZERO_REQUIREMENTS_COPY = "No hay requisitos registrados en este plan.";
const NO_REQUIREMENT_STATUS_COPY = "Sin estado declarado";
const CERTIFIED_CLARIFICATION =
  "Este estado es declarado manualmente y no representa una verificación técnica automática.";
const REQUIREMENT_NAME_MAX_LENGTH = 255;
const REQUIREMENT_STATUS_MAX_LENGTH = 30;

const STATUS_LABELS: Record<TrackingReadinessStatus, string> = {
  NOT_DEFINED: "No definido",
  REQUIREMENTS_DEFINED: "Requisitos definidos",
  CONFIGURATION_PENDING: "Configuración pendiente",
  CONFIGURED: "Configurado",
  VERIFICATION_PENDING: "Verificación pendiente",
  FAILED_VERIFICATION: "Verificación no superada",
  CERTIFIED: "Certificado (declarado)",
};

// Mirrors apps/api/app/tracking/transitions.py::TRACKING_PLAN_TRANSITIONS
// exactly — the backend remains authoritative regardless; this map only
// controls which buttons this panel offers, never what the backend
// accepts.
const TRACKING_PLAN_TRANSITIONS: Record<TrackingReadinessStatus, TrackingReadinessStatus[]> = {
  NOT_DEFINED: ["REQUIREMENTS_DEFINED"],
  REQUIREMENTS_DEFINED: ["CONFIGURATION_PENDING"],
  CONFIGURATION_PENDING: ["CONFIGURED", "FAILED_VERIFICATION"],
  CONFIGURED: ["VERIFICATION_PENDING"],
  VERIFICATION_PENDING: ["CERTIFIED", "FAILED_VERIFICATION"],
  FAILED_VERIFICATION: ["CONFIGURATION_PENDING"],
  CERTIFIED: [],
};

// Mirrors apps/api/app/tracking/transitions.py::REQUIREMENT_CREATION_ALLOWED_STATUSES.
const REQUIREMENT_CREATION_ALLOWED_STATUSES: ReadonlySet<TrackingReadinessStatus> = new Set([
  "NOT_DEFINED",
  "REQUIREMENTS_DEFINED",
  "CONFIGURATION_PENDING",
  "FAILED_VERIFICATION",
]);

function canMutateRequirementStatus(planStatus: TrackingReadinessStatus): boolean {
  // Mirrors REQUIREMENT_STATUS_MUTATION_ALLOWED_STATUSES — every state
  // except CERTIFIED.
  return planStatus !== "CERTIFIED";
}

function RequirementRow({
  requirement,
  planStatus,
  disabled,
  onUpdateStatus,
}: {
  requirement: TrackingRequirementPublic;
  planStatus: TrackingReadinessStatus;
  disabled: boolean;
  onUpdateStatus: (requirementId: string, status: string | null) => void;
}) {
  const [draft, setDraft] = useState(requirement.status ?? "");
  const canEdit = canMutateRequirementStatus(planStatus);

  // "Adjusting state when a prop changes" (React's own recommended
  // alternative to a setState-in-effect sync, already used by
  // WorkspaceSection/AiPreferencesSection/NotificationsSection) — resets
  // `draft` only when the server value itself changes, never on every
  // render, and never overwriting an in-progress edit or the effect of a
  // failed save (the confirmed value is what changes here, not `draft`
  // directly, so a pending edit the user is still typing is preserved
  // unless the server truly reports something new).
  const [syncedStatus, setSyncedStatus] = useState(requirement.status);
  if (requirement.status !== syncedStatus) {
    setSyncedStatus(requirement.status);
    setDraft(requirement.status ?? "");
  }

  function submit() {
    const trimmed = draft.trim();
    onUpdateStatus(requirement.id, trimmed.length === 0 ? null : trimmed);
  }

  return (
    <article className="panel deliverable-card">
      <span className="deliverable-icon">
        <Icon name="chart" size={19} />
      </span>
      <div>
        <h3>{requirement.name}</h3>
        {canEdit ? (
          <div className="settings-fields" style={{ marginTop: 8 }}>
            <input
              type="text"
              value={draft}
              maxLength={REQUIREMENT_STATUS_MAX_LENGTH}
              placeholder={NO_REQUIREMENT_STATUS_COPY}
              disabled={disabled}
              onChange={(event) => setDraft(event.target.value)}
              aria-label={`Estado de ${requirement.name}`}
            />
            <button type="button" className="button" disabled={disabled} onClick={submit}>
              Guardar estado
            </button>
          </div>
        ) : (
          <p className="muted small-text">{requirement.status ?? NO_REQUIREMENT_STATUS_COPY}</p>
        )}
        {/* MVP-24: identity-only (MVP-24A-R1) — Distribution public IDs
            only, never a claim that tracking fired or was verified for
            them. */}
        {requirement.associated_distribution_ids.length > 0 && (
          <p className="muted small-text" style={{ marginTop: 8 }}>
            Distribuciones asociadas: {requirement.associated_distribution_ids.join(", ")}
          </p>
        )}
      </div>
    </article>
  );
}

function PlanView({
  plan,
  pending,
  onTransition,
  onCreateRequirement,
  onUpdateRequirementStatus,
}: {
  plan: TrackingPlanPublic;
  pending: boolean;
  onTransition: (target: TrackingReadinessStatus) => void;
  onCreateRequirement: (name: string) => void;
  onUpdateRequirementStatus: (requirementId: string, status: string | null) => void;
}) {
  const [requirementDraft, setRequirementDraft] = useState("");
  const [confirmingCertify, setConfirmingCertify] = useState(false);
  const legalTransitions = TRACKING_PLAN_TRANSITIONS[plan.status];
  const canCreateRequirement = REQUIREMENT_CREATION_ALLOWED_STATUSES.has(plan.status);

  function requestTransition(target: TrackingReadinessStatus) {
    if (target === "CERTIFIED") {
      setConfirmingCertify(true);
      return;
    }
    onTransition(target);
  }

  function confirmCertify() {
    setConfirmingCertify(false);
    onTransition("CERTIFIED");
  }

  function submitRequirement() {
    const trimmed = requirementDraft.trim();
    if (trimmed.length === 0) return;
    onCreateRequirement(trimmed);
    setRequirementDraft("");
  }

  return (
    <>
      <section className="panel">
        <div className="section-heading">
          <h2>Estado declarado</h2>
        </div>
        <p>{STATUS_LABELS[plan.status]}</p>
        {plan.status === "CERTIFIED" && (
          <p className="muted small-text" style={{ marginTop: 8 }}>
            {CERTIFIED_CLARIFICATION}
          </p>
        )}
        {legalTransitions.length > 0 && (
          <div className="settings-form-actions" style={{ marginTop: 12 }}>
            {legalTransitions.map((target) => (
              <button
                key={target}
                type="button"
                className="button"
                disabled={pending}
                onClick={() => requestTransition(target)}
              >
                Marcar: {STATUS_LABELS[target]}
              </button>
            ))}
          </div>
        )}
        {confirmingCertify && (
          <div className="panel" role="alertdialog" aria-label="Confirmar certificación" style={{ marginTop: 12 }}>
            <p>{CERTIFIED_CLARIFICATION}</p>
            <div className="settings-form-actions" style={{ marginTop: 8 }}>
              <button type="button" className="button primary" disabled={pending} onClick={confirmCertify}>
                Confirmar
              </button>
              <button type="button" className="button" disabled={pending} onClick={() => setConfirmingCertify(false)}>
                Cancelar
              </button>
            </div>
          </div>
        )}
      </section>

      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Requisitos</h2>
      </div>
      {plan.requirements.length === 0 ? (
        <p className="muted small-text">{ZERO_REQUIREMENTS_COPY}</p>
      ) : (
        <div className="deliverables-grid">
          {plan.requirements.map((requirement) => (
            <RequirementRow
              key={requirement.id}
              requirement={requirement}
              planStatus={plan.status}
              disabled={pending}
              onUpdateStatus={onUpdateRequirementStatus}
            />
          ))}
        </div>
      )}

      {canCreateRequirement && (
        <section className="panel" style={{ marginTop: 16 }}>
          <div className="settings-field">
            <label htmlFor="tracking-requirement-name">Nombre del requisito</label>
            <input
              id="tracking-requirement-name"
              type="text"
              value={requirementDraft}
              maxLength={REQUIREMENT_NAME_MAX_LENGTH}
              disabled={pending}
              onChange={(event) => setRequirementDraft(event.target.value)}
            />
          </div>
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="button primary"
              disabled={pending || requirementDraft.trim().length === 0}
              onClick={submitRequirement}
            >
              Añadir requisito
            </button>
          </div>
        </section>
      )}
    </>
  );
}

export function TrackingPanel({
  campaignId,
  active,
  refreshToken,
}: {
  campaignId: string;
  active: boolean;
  refreshToken: number;
}) {
  const [result, setResult] = useState<Result | null>(null);
  const [pending, setPending] = useState(false);
  const [mutationError, setMutationError] = useState("");
  const requestedTokenRef = useRef<number | null>(null);

  function applyResponse(currentToken: number, response: TrackingResponse) {
    setResult({ token: currentToken, status: "ready", plan: response.plan });
    setMutationError("");
  }

  // Manual retry (button click, not an effect) — no cancellation guard
  // needed for a one-off user-initiated action, matching ResearchPanel's
  // own `retry` precedent exactly. Repeats only the GET request.
  function retry() {
    requestedTokenRef.current = refreshToken;
    getTracking(campaignId)
      .then((response) => setResult({ token: refreshToken, status: "ready", plan: response.plan }))
      .catch((error) => {
        requestedTokenRef.current = null;
        setResult({ token: refreshToken, status: "error", message: describeCampaignError(error) });
      });
  }

  useEffect(() => {
    if (!active || requestedTokenRef.current === refreshToken) return;
    let cancelled = false;
    requestedTokenRef.current = refreshToken;
    getTracking(campaignId)
      .then((response) => {
        if (!cancelled) setResult({ token: refreshToken, status: "ready", plan: response.plan });
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

  async function runMutation(currentToken: number, action: () => Promise<TrackingResponse>) {
    if (pending) return;
    setPending(true);
    setMutationError("");
    try {
      const response = await action();
      applyResponse(currentToken, response);
    } catch (error) {
      setMutationError(describeCampaignError(error));
    } finally {
      setPending(false);
    }
  }

  const loading = result === null || result.token !== refreshToken;

  if (loading) {
    return (
      <section className="panel">
        <p className="muted small-text" role="status">
          Cargando tracking…
        </p>
      </section>
    );
  }

  if (result.status === "error") {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="chart" size={32} />
        </span>
        <h2>No pudimos cargar el tracking en este momento.</h2>
        <p>
          {result.message}{" "}
          <button type="button" className="auth-text-button" onClick={retry}>
            Reintentar
          </button>
        </p>
      </section>
    );
  }

  const currentToken = result.token;

  if (result.plan === null) {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="chart" size={32} />
        </span>
        <h2>{EMPTY_COPY}</h2>
        <p className="muted small-text">{EMPTY_SECONDARY_COPY}</p>
        <div className="settings-form-actions" style={{ marginTop: 12 }}>
          <button
            type="button"
            className="button primary"
            disabled={pending}
            onClick={() => runMutation(currentToken, () => createTrackingPlan(campaignId))}
          >
            {CREATE_PLAN_LABEL}
          </button>
        </div>
        {mutationError && (
          <p role="alert" className="settings-feedback">
            {mutationError}
          </p>
        )}
      </section>
    );
  }

  return (
    <>
      <PlanView
        plan={result.plan}
        pending={pending}
        onTransition={(target) =>
          runMutation(currentToken, () => patchTracking(campaignId, { operation: "TRANSITION_PLAN", target_status: target }))
        }
        onCreateRequirement={(name) =>
          runMutation(currentToken, () => createTrackingRequirement(campaignId, name))
        }
        onUpdateRequirementStatus={(requirementId, status) =>
          runMutation(currentToken, () =>
            patchTracking(campaignId, { operation: "UPDATE_REQUIREMENT_STATUS", requirement_id: requirementId, status }),
          )
        }
      />
      {mutationError && (
        <p role="alert" className="settings-feedback">
          {mutationError}
        </p>
      )}
    </>
  );
}
