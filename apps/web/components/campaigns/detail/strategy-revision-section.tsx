"use client";

// MVP-30B: Governed Strategy Revision surface (frozen MVP-30A/-30A-R1
// contract). Every mutation goes through the real, authenticated,
// CSRF-protected backend route
// (apps/api/app/orchestration/strategy_revision_router.py) — no
// optimistic/fake state anywhere in this file.
//
// Self-contained (mirrors StrategicDecisionSection/StrategicApprovalStatus's
// own pattern): fetches and mutates independently of StrategyPanel's own
// current-Strategy fetch, since eligibility depends on cross-referencing
// two OTHER bounded contexts' own read surfaces (StrategicDecision,
// StrategicApproval) that StrategyPanel has no reason to know about.
//
// Eligibility is derived client-side ONLY for UX (which Approval, if any,
// is currently ADOPT + current + APPROVED + unconsumed) — the backend
// remains fully authoritative; a stale/incorrect client computation can
// only ever produce a rejected request (StrategyRevisionNotEligibleError /
// StrategicApprovalAlreadyConsumedError / StrategyRevisionBaseStaleError),
// never an unauthorized write. No heuristic (latest/MAX/first row)
// selection is ever made automatically — the human explicitly picks one
// eligible Approval from a real list (MVP-30A-R1 §38).

import { useCallback, useEffect, useRef, useState } from "react";
import { getStrategicApprovals } from "@/lib/api/strategic-approvals";
import { getStrategicDecisions } from "@/lib/api/strategic-decisions";
import { getStrategyHistory, reviseStrategy } from "@/lib/api/strategy-revisions";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { formatCampaignDate } from "@/lib/campaigns/status";
import type { StrategicApprovalPublic } from "@/types/strategic-approvals";
import type { StrategyHistoryItem } from "@/types/strategy-revisions";

type Result =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; history: StrategyHistoryItem[]; eligibleApprovals: StrategicApprovalPublic[] };

function isOwnerOrAdmin(role: string | null): boolean {
  return role === "OWNER" || role === "ADMIN";
}

export function StrategyRevisionSection({
  campaignId,
  currentStrategyId,
  role,
  onRevised,
}: {
  campaignId: string;
  currentStrategyId: string;
  role: string | null;
  onRevised: () => void;
}) {
  const [result, setResult] = useState<Result>({ status: "loading" });
  const [selectedApprovalId, setSelectedApprovalId] = useState("");
  const [summary, setSummary] = useState("");
  const [positioningStatement, setPositioningStatement] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [pending, setPending] = useState(false);
  const [mutationError, setMutationError] = useState("");
  const requestedRef = useRef(false);
  const canRevise = isOwnerOrAdmin(role);

  const load = useCallback(() => {
    requestedRef.current = true;
    Promise.all([getStrategicApprovals(campaignId), getStrategicDecisions(campaignId), getStrategyHistory(campaignId)])
      .then(([approvals, decisions, history]) => {
        const consumedApprovalIds = new Set(
          history.items.filter((item) => item.revision !== null).map((item) => item.revision!.strategic_approval_id),
        );
        const decisionById = new Map(decisions.map((decision) => [decision.id, decision]));
        const eligibleApprovals = approvals.filter((approval) => {
          if (approval.outcome !== "APPROVED") return false;
          if (consumedApprovalIds.has(approval.id)) return false;
          const decision = decisionById.get(approval.strategic_decision_id);
          return decision !== undefined && decision.decision_type === "ADOPT" && decision.current;
        });
        setResult({ status: "ready", history: history.items, eligibleApprovals });
      })
      .catch((error) => setResult({ status: "error", message: describeCampaignError(error) }));
  }, [campaignId]);

  useEffect(() => {
    if (requestedRef.current) return;
    load();
  }, [load]);

  async function submit() {
    if (pending || !selectedApprovalId) return;
    setPending(true);
    setMutationError("");
    try {
      await reviseStrategy(campaignId, currentStrategyId, selectedApprovalId, summary.trim(), positioningStatement.trim());
      setSummary("");
      setPositioningStatement("");
      setSelectedApprovalId("");
      setShowForm(false);
      load();
      onRevised();
    } catch (error) {
      setMutationError(describeCampaignError(error));
    } finally {
      setPending(false);
    }
  }

  if (result.status === "loading") {
    return (
      <p className="muted small-text" role="status">
        Cargando revisión estratégica…
      </p>
    );
  }

  if (result.status === "error") {
    return (
      <p className="muted small-text" role="alert">
        No pudimos cargar la revisión estratégica. {result.message}{" "}
        <button type="button" className="auth-text-button" onClick={load}>
          Reintentar
        </button>
      </p>
    );
  }

  const { history, eligibleApprovals } = result;
  const historical = history.filter((item) => item.strategy.id !== currentStrategyId);

  return (
    <div className="panel" style={{ marginTop: 24 }}>
      <h4>Revisión estratégica gobernada</h4>
      {canRevise && eligibleApprovals.length > 0 && !showForm && (
        <div className="settings-form-actions" style={{ marginTop: 8 }}>
          <button type="button" className="button primary" onClick={() => setShowForm(true)}>
            Registrar revisión de estrategia
          </button>
        </div>
      )}
      {canRevise && eligibleApprovals.length === 0 && (
        <p className="muted small-text">
          No hay ninguna Aprobación estratégica vigente y disponible para autorizar una revisión.
        </p>
      )}
      {showForm && (
        <div className="panel" style={{ marginTop: 8 }}>
          <div className="settings-field">
            <label htmlFor="revision-approval">Aprobación estratégica que autoriza esta revisión</label>
            <select
              id="revision-approval"
              value={selectedApprovalId}
              disabled={pending}
              onChange={(event) => setSelectedApprovalId(event.target.value)}
            >
              <option value="">Selecciona una aprobación…</option>
              {eligibleApprovals.map((approval) => (
                <option key={approval.id} value={approval.id}>
                  {approval.id} · Aprobada {formatCampaignDate(approval.created_at)}
                </option>
              ))}
            </select>
          </div>
          <div className="settings-field">
            <label htmlFor="revision-summary">Nuevo resumen de estrategia</label>
            <textarea id="revision-summary" value={summary} disabled={pending} onChange={(event) => setSummary(event.target.value)} />
          </div>
          <div className="settings-field">
            <label htmlFor="revision-positioning">Nuevo posicionamiento</label>
            <textarea
              id="revision-positioning"
              value={positioningStatement}
              disabled={pending}
              onChange={(event) => setPositioningStatement(event.target.value)}
            />
          </div>
          <p className="muted small-text">
            Esta revisión reemplaza el estado vigente de la estrategia y el posicionamiento — no crea hipótesis,
            experimentos, ni ningún otro contenido, y no autoriza ejecución externa.
          </p>
          <div className="settings-form-actions" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="button primary"
              disabled={pending || !selectedApprovalId || summary.trim().length === 0 || positioningStatement.trim().length === 0}
              onClick={submit}
            >
              Confirmar revisión
            </button>
            <button type="button" className="button" disabled={pending} onClick={() => setShowForm(false)}>
              Cancelar
            </button>
          </div>
        </div>
      )}
      {historical.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary>Historial de versiones ({historical.length})</summary>
          <div className="deliverables-grid" style={{ marginTop: 8 }}>
            {historical.map((item) => (
              <article key={item.strategy.id} className="panel deliverable-card">
                <div>
                  <h3>v{item.strategy.version} · {item.strategy.origin === "REVISION" ? "Revisión gobernada" : "Borrador inicial"}</h3>
                  <p>{item.strategy.summary}</p>
                  <p className="muted small-text">Registrada {formatCampaignDate(item.strategy.created_at)}</p>
                  {item.revision && (
                    <p className="muted small-text">Aprobación: {item.revision.strategic_approval_id}</p>
                  )}
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
