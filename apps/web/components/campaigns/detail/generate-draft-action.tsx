"use client";

// MVP-05A: the "Generar borrador" action and its truthful state
// management. Deliberately self-contained (its own progress fetch, its
// own submit lifecycle) so `CampaignDetail` does not need to know
// anything about orchestration beyond "here are this campaign's runs."
//
// CampaignRun.status === "RUNNING" is NEVER treated as "currently
// executing" (MVP-04's synchronous Research->Plan bootstrap leaves the
// run RUNNING forever afterward, successful or partially failed, because
// CONTENT onward stays PENDING). The only true in-flight signal here is
// the local `submitting` flag around this component's own
// initialize->start request chain.

import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { getCampaignRunProgress, initializeCampaignRun, startCampaignRun } from "@/lib/api/orchestration";
import { campaignRunStatusLabel } from "@/lib/campaigns/status";
import type { CampaignRunPublic } from "@/types/campaign";
import type { RunProgressPublic } from "@/types/orchestration";

const MICROCOPY =
  "Esto generará un borrador inicial de Investigación, Audiencia, Estrategia y Planificación a partir de los datos de tu campaña. No incluye investigación externa ni ha sido validado.";

const IN_FLIGHT_COPY = "Generando tu borrador inicial… esto puede tardar unos segundos.";

const SUCCESS_COPY = "Borrador inicial generado.";

const FAILURE_COPY =
  "No se pudo completar el borrador inicial. Parte del proceso pudo haberse guardado, pero esta ejecución no puede reintentarse desde esta versión.";

type ProgressState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; progress: RunProgressPublic };

function hasFailedStage(progress: RunProgressPublic): boolean {
  return progress.stages.some((stage) => stage.status === "FAILED");
}

// Truthful "did the MVP-04 bootstrap actually finish" signal — never
// inferred from `current_stage` alone (it skips over a FAILED stage to
// report the next PENDING one, which would misreport a partial failure
// as if PLAN had run).
function planCompleted(progress: RunProgressPublic): boolean {
  return progress.stages.some((stage) => stage.stage === "PLAN" && stage.status === "COMPLETED");
}

export function GenerateDraftAction({
  campaignId,
  runs,
  onGenerated,
}: {
  campaignId: string;
  runs: CampaignRunPublic[];
  /** MVP-05B: called once after each initialize->start attempt reaches
   * the backend (success or partial failure alike) — the caller uses
   * this to invalidate any Research/Audience/Strategy/Plan output it
   * cached before this campaign had a draft, so the next time those
   * tabs are opened they re-fetch instead of showing a stale
   * pre-generation empty state forever. */
  onGenerated?: () => void;
}) {
  const [state, setState] = useState<ProgressState>({ status: "loading" });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");

  const runsCount = runs.length;
  const soleRun = runsCount === 1 ? runs[0] : null;

  // Purely derived from props, at render time — never routed through the
  // async progress-loading state machine below, since no fetch is
  // involved in recognizing an unsupported run count.
  const unsupportedMessage =
    runsCount === 0
      ? "Todavía no hay una ejecución registrada para esta campaña."
      : runsCount > 1
        ? "Esta campaña tiene más de una ejecución registrada; esta versión no admite ese caso."
        : null;

  useEffect(() => {
    if (!soleRun) return;
    let active = true;
    getCampaignRunProgress(campaignId, soleRun.id)
      .then((progress) => {
        if (active) setState({ status: "ready", progress });
      })
      .catch((error) => {
        if (active) setState({ status: "error", message: describeCampaignError(error) });
      });
    return () => {
      active = false;
    };
  }, [campaignId, soleRun]);

  async function refreshProgress(runId: string): Promise<RunProgressPublic> {
    const progress = await getCampaignRunProgress(campaignId, runId);
    setState({ status: "ready", progress });
    return progress;
  }

  async function handleGenerate() {
    if (submitting || !soleRun) return;
    if (state.status !== "ready" || state.progress.run_status !== "CREATED") return;

    setSubmitting(true);
    setSubmitError("");
    try {
      await initializeCampaignRun(campaignId, soleRun.id);
      await startCampaignRun(campaignId, soleRun.id);
      await refreshProgress(soleRun.id);
    } catch (error) {
      // The backend may already have committed earlier stages before this
      // failure (a genuine bootstrap 500), or another tab/click may have
      // already started this same run (409 INVALID_LIFECYCLE_TRANSITION).
      // Either way, a truthful refetch — never an assumption — decides
      // what the user sees next; CTA visibility below is driven only by
      // the refreshed `run_status`/`stages`, never by which error fired.
      try {
        await refreshProgress(soleRun.id);
      } catch {
        setSubmitError(describeCampaignError(error));
      }
    } finally {
      setSubmitting(false);
      onGenerated?.();
    }
  }

  function retryProgressLoad() {
    if (!soleRun) return;
    setState({ status: "loading" });
    getCampaignRunProgress(campaignId, soleRun.id)
      .then((progress) => setState({ status: "ready", progress }))
      .catch((error) => setState({ status: "error", message: describeCampaignError(error) }));
  }

  if (unsupportedMessage) {
    return (
      <section className="panel">
        <p className="muted small-text">{unsupportedMessage}</p>
      </section>
    );
  }

  if (state.status === "loading") {
    return (
      <section className="panel">
        <p className="muted small-text" role="status">
          Cargando estado del borrador…
        </p>
      </section>
    );
  }

  if (state.status === "error") {
    return (
      <section className="panel">
        <p role="alert">
          {state.message}{" "}
          <button type="button" className="auth-text-button" onClick={retryProgressLoad}>
            Reintentar
          </button>
        </p>
      </section>
    );
  }

  const { progress } = state;
  const stillCreated = progress.run_status === "CREATED";
  const failed = hasFailedStage(progress);
  const completed = planCompleted(progress);

  return (
    <section className="panel">
      {stillCreated || submitting ? (
        <>
          <p className="muted small-text">{MICROCOPY}</p>
          <div className="composer-footer">
            <p role="status" aria-live="polite" aria-atomic="true">
              {submitting ? IN_FLIGHT_COPY : submitError}
            </p>
            <button
              type="button"
              className="button primary"
              aria-busy={submitting}
              disabled={submitting}
              onClick={handleGenerate}
            >
              <Icon name="spark" size={18} />
              {submitting ? "Generando…" : "Generar borrador"}
            </button>
          </div>
        </>
      ) : failed ? (
        <p role="alert">{FAILURE_COPY}</p>
      ) : completed ? (
        <p className="muted small-text">{SUCCESS_COPY}</p>
      ) : (
        <p className="muted small-text">Estado de la ejecución: {campaignRunStatusLabel(progress.run_status)}.</p>
      )}
    </section>
  );
}
