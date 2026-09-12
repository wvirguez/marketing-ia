"use client";

// MVP-08B: real, read-only Tracking panel for the campaign "Tracking" tab.
// Fetches GET /campaigns/{campaignId}/tracking directly. The backend also
// exposes a PATCH route (Plan transition / Requirement status update),
// but this panel never calls it — no write control, no transition
// button, no "certify" action exists anywhere in this file.
//
// Hard invariants preserved throughout (apps/api/app/tracking/models.py,
// apps/api/app/tracking/service.py): TRACKING PLAN != TRACKING
// REQUIREMENT. TRACKING STATUS != TECHNICAL VERIFICATION. CERTIFIED !=
// SYSTEM-VERIFIED. CERTIFIED != MEASUREMENT RESULT. `plan.status` is a
// manually-updated, self-reported workflow marker — the backend's own
// service docstring states "CERTIFIED is a manual, self-declared
// attestation only" with zero automated/network verification behind it.
// This panel therefore renders status as plain descriptive metadata
// under "Estado declarado," never as a colored success/approval badge,
// and adds an explicit clarification for CERTIFIED specifically.
//
// TRACKING PRODUCTION GAP (preserved, not solved here): no production
// caller currently creates TrackingPlan/TrackingRequirement rows, so
// every real campaign today returns `{ plan: null }` — the empty state
// below is written to be truthful about that, never implying pixel
// installation, provider connection, automatic event collection, or a
// pending/failed verification unless a Plan actually reports it.

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { getTracking } from "@/lib/api/tracking";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import type { TrackingPlanPublic, TrackingReadinessStatus, TrackingRequirementPublic } from "@/types/tracking";

type Result =
  | { token: number; status: "error"; message: string }
  | { token: number; status: "ready"; plan: TrackingPlanPublic | null };

const EMPTY_COPY = "Aún no se ha definido un plan de tracking para esta campaña.";
const EMPTY_SECONDARY_COPY =
  "Cuando exista un plan, aquí podrás consultar sus requisitos y el estado declarado.";
const ZERO_REQUIREMENTS_COPY = "No hay requisitos registrados en este plan.";
const NO_REQUIREMENT_STATUS_COPY = "Sin estado declarado";
const CERTIFIED_CLARIFICATION =
  "Este estado es declarado manualmente y no representa una verificación técnica automática.";

const STATUS_LABELS: Record<TrackingReadinessStatus, string> = {
  NOT_DEFINED: "No definido",
  REQUIREMENTS_DEFINED: "Requisitos definidos",
  CONFIGURATION_PENDING: "Configuración pendiente",
  CONFIGURED: "Configurado",
  VERIFICATION_PENDING: "Verificación pendiente",
  FAILED_VERIFICATION: "Verificación no superada",
  CERTIFIED: "Certificado (declarado)",
};

function RequirementCard({ requirement }: { requirement: TrackingRequirementPublic }) {
  return (
    <article className="panel deliverable-card">
      <span className="deliverable-icon">
        <Icon name="chart" size={19} />
      </span>
      <div>
        <h3>{requirement.name}</h3>
        <p className="muted small-text">{requirement.status ?? NO_REQUIREMENT_STATUS_COPY}</p>
      </div>
    </article>
  );
}

function PlanView({ plan }: { plan: TrackingPlanPublic }) {
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
      </section>

      <div className="section-heading" style={{ marginTop: 24 }}>
        <h2>Requisitos</h2>
      </div>
      {plan.requirements.length === 0 ? (
        <p className="muted small-text">{ZERO_REQUIREMENTS_COPY}</p>
      ) : (
        <div className="deliverables-grid">
          {plan.requirements.map((requirement) => (
            <RequirementCard key={requirement.id} requirement={requirement} />
          ))}
        </div>
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
  const requestedTokenRef = useRef<number | null>(null);

  // Manual retry (button click, not an effect) — no cancellation guard
  // needed for a one-off user-initiated action, matching
  // ResearchPanel's own `retry` precedent exactly. Repeats only the GET
  // request — no PATCH, no transition, no certification action exists.
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

  if (result.plan === null) {
    return (
      <section className="panel workspace-empty">
        <span className="workspace-empty-symbol">
          <Icon name="chart" size={32} />
        </span>
        <h2>{EMPTY_COPY}</h2>
        <p className="muted small-text">{EMPTY_SECONDARY_COPY}</p>
      </section>
    );
  }

  return <PlanView plan={result.plan} />;
}
