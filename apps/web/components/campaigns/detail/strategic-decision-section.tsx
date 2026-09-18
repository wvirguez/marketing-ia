"use client";

// MVP-28B: real StrategicDecision surface for one accepted
// StrategicRecommendationCandidate (frozen MVP-28A/-R1/-R2 contract).
// Every mutation goes through the real, authenticated, CSRF-protected
// backend routes (apps/api/app/orchestration/strategic_decision_router.py)
// — no optimistic/fake state anywhere in this file.
//
// Self-contained (mirrors LearningQualification/CommercialPanel's own
// patterns): fetches and mutates independently of LearningPanel's central
// state machine, since StrategicDecision is a distinct bounded context
// (orchestration, not learning) with its own API surface, never part of
// `GET /learning`'s own response shape.
//
// Hard invariants preserved throughout (apps/api/app/orchestration/models.py,
// service.py, MVP-28A/-R1/-R2): "current" is derived (superseded_at IS
// NULL), never a second persisted status. Recording (first decision) and
// superseding (replacing the current one) are two distinct, never-conflated
// actions — replacement always creates a fresh row and marks the original
// historical, never an in-place edit. Recording ADOPT/DEFER/DECLINE never
// implies Strategic Approval, a Strategy revision, or execution
// authorization — none of that language appears anywhere below. Read-only
// (non-OWNER/ADMIN) users see status only, never a mutation control.

import { useCallback, useEffect, useRef, useState } from "react";
import { getStrategicApprovals, recordStrategicApproval } from "@/lib/api/strategic-approvals";
import {
  getStrategicDecisions,
  recordStrategicDecision,
  supersedeStrategicDecision,
} from "@/lib/api/strategic-decisions";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { formatCampaignDate } from "@/lib/campaigns/status";
import type { StrategicApprovalOutcome, StrategicApprovalPublic } from "@/types/strategic-approvals";
import type { StrategicDecisionPublic, StrategicDecisionType } from "@/types/strategic-decisions";

type Result =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; decisions: StrategicDecisionPublic[]; approvals: StrategicApprovalPublic[] };

const APPROVAL_OUTCOME_LABELS: Record<StrategicApprovalOutcome, string> = {
  APPROVED: "Aprobada",
  REJECTED: "Rechazada",
};

const DECISION_TYPE_LABELS: Record<StrategicDecisionType, string> = {
  ADOPT: "Adoptar",
  DEFER: "Postergar",
  DECLINE: "No proceder",
};

function isOwnerOrAdmin(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN";
}

function DecisionForm({
  pending,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  pending: boolean;
  submitLabel: string;
  onSubmit: (decisionType: StrategicDecisionType, statement: string) => void;
  onCancel?: () => void;
}) {
  const [decisionType, setDecisionType] = useState<StrategicDecisionType>("ADOPT");
  const [statement, setStatement] = useState("");
  const canSubmit = statement.trim().length > 0 && !pending;

  return (
    <div className="panel" style={{ marginTop: 8 }}>
      <div className="settings-field">
        <label htmlFor={`decision-type-${submitLabel}`}>Tipo de decisión</label>
        <select
          id={`decision-type-${submitLabel}`}
          value={decisionType}
          disabled={pending}
          onChange={(event) => setDecisionType(event.target.value as StrategicDecisionType)}
        >
          {(Object.keys(DECISION_TYPE_LABELS) as StrategicDecisionType[]).map((type) => (
            <option key={type} value={type}>
              {DECISION_TYPE_LABELS[type]}
            </option>
          ))}
        </select>
      </div>
      <div className="settings-field">
        <label htmlFor={`decision-statement-${submitLabel}`}>Justificación</label>
        <textarea
          id={`decision-statement-${submitLabel}`}
          value={statement}
          disabled={pending}
          onChange={(event) => setStatement(event.target.value)}
        />
      </div>
      <p className="muted small-text">
        Esta decisión no aprueba la Estrategia ni autoriza ejecución — registra únicamente el rumbo que la
        gobernanza de la campaña elige seguir frente a esta recomendación.
      </p>
      <div className="settings-form-actions" style={{ marginTop: 8 }}>
        <button
          type="button"
          className="button primary"
          disabled={!canSubmit}
          onClick={() => onSubmit(decisionType, statement.trim())}
        >
          {submitLabel}
        </button>
        {onCancel && (
          <button type="button" className="button" disabled={pending} onClick={onCancel}>
            Cancelar
          </button>
        )}
      </div>
    </div>
  );
}

function ApprovalStatus({
  campaignId,
  decision,
  approval,
  canDecide,
  isCurrent,
  onRecorded,
}: {
  campaignId: string;
  decision: StrategicDecisionPublic;
  approval: StrategicApprovalPublic | null;
  canDecide: boolean;
  isCurrent: boolean;
  onRecorded: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function decide(outcome: StrategicApprovalOutcome) {
    if (pending) return;
    setPending(true);
    setError("");
    try {
      await recordStrategicApproval(campaignId, decision.id, outcome);
      onRecorded();
    } catch (err) {
      setError(describeCampaignError(err));
    } finally {
      setPending(false);
    }
  }

  if (approval) {
    return (
      <p className="muted small-text">
        Aprobación estratégica: {APPROVAL_OUTCOME_LABELS[approval.outcome]} ·{" "}
        {formatCampaignDate(approval.created_at)}
      </p>
    );
  }

  const canRecordApproval = canDecide && isCurrent && decision.decision_type === "ADOPT";

  return (
    <div>
      <p className="muted small-text">Sin aprobación estratégica registrada.</p>
      {canRecordApproval && (
        <div className="settings-form-actions" style={{ marginTop: 4 }}>
          <button type="button" className="button primary" disabled={pending} onClick={() => decide("APPROVED")}>
            Aprobar
          </button>
          <button type="button" className="button" disabled={pending} onClick={() => decide("REJECTED")}>
            Rechazar
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="settings-feedback">
          {error}
        </p>
      )}
    </div>
  );
}

export function StrategicDecisionSection({
  campaignId,
  recommendationId,
  role,
}: {
  campaignId: string;
  recommendationId: string;
  role: string | null;
}) {
  const [result, setResult] = useState<Result>({ status: "loading" });
  const [pending, setPending] = useState(false);
  const [mutationError, setMutationError] = useState("");
  const [replacing, setReplacing] = useState(false);
  const requestedRef = useRef<string | null>(null);
  const canDecide = isOwnerOrAdmin(role);

  const load = useCallback(() => {
    requestedRef.current = recommendationId;
    Promise.all([getStrategicDecisions(campaignId), getStrategicApprovals(campaignId)])
      .then(([decisions, approvals]) =>
        setResult({
          status: "ready",
          decisions: decisions.filter((d) => d.strategic_recommendation_candidate_id === recommendationId),
          approvals,
        }),
      )
      .catch((error) => setResult({ status: "error", message: describeCampaignError(error) }));
  }, [campaignId, recommendationId]);

  useEffect(() => {
    if (requestedRef.current === recommendationId) return;
    load();
  }, [recommendationId, load]);

  async function runMutation(action: () => Promise<unknown>) {
    if (pending) return;
    setPending(true);
    setMutationError("");
    try {
      await action();
      load();
      setReplacing(false);
    } catch (error) {
      setMutationError(describeCampaignError(error));
    } finally {
      setPending(false);
    }
  }

  if (result.status === "loading") {
    return (
      <p className="muted small-text" role="status">
        Cargando decisión estratégica…
      </p>
    );
  }

  if (result.status === "error") {
    return (
      <p className="muted small-text" role="alert">
        No pudimos cargar la decisión estratégica. {result.message}{" "}
        <button type="button" className="auth-text-button" onClick={load}>
          Reintentar
        </button>
      </p>
    );
  }

  const current = result.decisions.find((d) => d.current) ?? null;
  const historical = result.decisions.filter((d) => !d.current);
  const approvalByDecisionId = new Map(result.approvals.map((approval) => [approval.strategic_decision_id, approval]));

  return (
    <div className="panel" style={{ marginTop: 8 }}>
      <h4>Decisión estratégica</h4>
      {current === null ? (
        <>
          <p className="muted small-text">
            Aún no se ha registrado una decisión estratégica para esta recomendación aceptada.
          </p>
          {canDecide && (
            <DecisionForm
              pending={pending}
              submitLabel="Registrar decisión"
              onSubmit={(decisionType, statement) =>
                runMutation(() => recordStrategicDecision(campaignId, recommendationId, decisionType, statement))
              }
            />
          )}
        </>
      ) : (
        <>
          <p>
            {DECISION_TYPE_LABELS[current.decision_type]}: {current.statement}
          </p>
          <p className="muted small-text">Vigente · Registrada {formatCampaignDate(current.created_at)}</p>
          <ApprovalStatus
            campaignId={campaignId}
            decision={current}
            approval={approvalByDecisionId.get(current.id) ?? null}
            canDecide={canDecide}
            isCurrent
            onRecorded={load}
          />
          {canDecide && !replacing && (
            <div className="settings-form-actions" style={{ marginTop: 8 }}>
              <button type="button" className="button" disabled={pending} onClick={() => setReplacing(true)}>
                Reemplazar esta decisión
              </button>
            </div>
          )}
          {replacing && (
            <DecisionForm
              pending={pending}
              submitLabel="Confirmar reemplazo"
              onCancel={() => setReplacing(false)}
              onSubmit={(decisionType, statement) =>
                runMutation(() => supersedeStrategicDecision(campaignId, current.id, decisionType, statement))
              }
            />
          )}
        </>
      )}
      {historical.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary>Historial de decisiones ({historical.length})</summary>
          <div className="deliverables-grid" style={{ marginTop: 8 }}>
            {historical.map((decision) => (
              <article key={decision.id} className="panel deliverable-card">
                <div>
                  <h3>{DECISION_TYPE_LABELS[decision.decision_type]}</h3>
                  <p>{decision.statement}</p>
                  <p className="muted small-text">
                    Reemplazada el {decision.superseded_at ? formatCampaignDate(decision.superseded_at) : ""}
                  </p>
                  <ApprovalStatus
                    campaignId={campaignId}
                    decision={decision}
                    approval={approvalByDecisionId.get(decision.id) ?? null}
                    canDecide={canDecide}
                    isCurrent={false}
                    onRecorded={load}
                  />
                </div>
              </article>
            ))}
          </div>
        </details>
      )}
      {mutationError && (
        <p role="alert" className="settings-feedback">
          {mutationError}
        </p>
      )}
    </div>
  );
}
