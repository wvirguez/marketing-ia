"use client";

// MVP-05A: the "Generar borrador" action and its truthful state
// management. Deliberately self-contained (its own progress fetch, its
// own submit lifecycle) so `CampaignDetail` does not need to know
// anything about orchestration beyond "here are this campaign's runs."
//
// CampaignRun.status === "RUNNING" is NEVER treated as "currently
// executing" (the synchronous Research->Content bootstrap leaves the run
// RUNNING forever afterward, successful or partially failed, because
// CREATIVE onward stays PENDING). The only true in-flight signal here is
// the local `submitting` flag around this component's own
// initialize->start request chain. MVP-06B: `getDraftPresentationState`
// (lib/campaigns/draft-progress.ts) is the truthful "did the bootstrap
// actually finish" signal this component and `CampaignRuns` both use —
// never inferred from `current_stage`/`run_status` alone.

import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { getCampaignRunProgress, initializeCampaignRun, startCampaignRun } from "@/lib/api/orchestration";
import {
  businessStageLabel,
  firstFailedBootstrapStage,
  getDraftPresentationState,
  type DraftPresentationState,
} from "@/lib/campaigns/draft-progress";
import { campaignRunStatusLabel } from "@/lib/campaigns/status";
import type { CampaignRunPublic } from "@/types/campaign";
import type { RunProgressPublic } from "@/types/orchestration";

const MICROCOPY =
  "Esto generará un borrador inicial de Investigación, Audiencia, Estrategia, Planificación y Contenido a partir de los datos de tu campaña. No incluye investigación externa ni ha sido validado.";

const IN_FLIGHT_COPY = "Generando tu borrador inicial… esto puede tardar unos segundos.";

const SUCCESS_COPY = "Borrador inicial generado.";

// MVP-06B §16: generic, truthful fallback — used only if a `FAILED`
// presentation state is somehow reached without a resolvable bootstrap
// stage (defensive; should not occur given `getDraftPresentationState`'s
// own contract).
const GENERIC_FAILURE_COPY =
  "No se pudo completar el borrador inicial. Parte del proceso pudo haberse guardado, pero esta ejecución no puede reintentarse desde esta versión.";

// MVP-06B §15/§17: names the failed BUSINESS stage (never an agent
// identifier), states plainly that earlier completed stages *may* still
// be available (never "nothing was saved," never a false guarantee that
// everything before it definitely persisted — CONTENT's own per-PlanItem
// atomicity means even the failed stage itself may have partial rows),
// and that this run cannot currently be retried.
function buildFailureCopy(progress: RunProgressPublic): string {
  const failedStage = firstFailedBootstrapStage(progress);
  if (!failedStage) return GENERIC_FAILURE_COPY;
  return (
    `No se pudo completar el borrador inicial: la etapa de ${businessStageLabel(failedStage.stage)} presentó un ` +
    "problema. Las etapas anteriores que sí se completaron pueden seguir disponibles en sus secciones. Esta " +
    "ejecución no puede reintentarse desde esta versión."
  );
}

type ProgressState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; progress: RunProgressPublic };

export function GenerateDraftAction({
  campaignId,
  runs,
  onGenerated,
  onDraftPresentationChange,
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
  /** MVP-06B: reports this component's own derived bootstrap presentation
   * state for its sole run, so `CampaignRuns` (a sibling under the same
   * Overview tab) can present that same run without contradicting this
   * component's own success/failure copy — `null` whenever no reliable
   * derived state is available (loading/error/unsupported run count). */
  onDraftPresentationChange?: (info: { runId: string; state: DraftPresentationState } | null) => void;
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

  // MVP-06B: reports the derived presentation state upward whenever it
  // changes — a pure function of `state`/`soleRun`, never a new fetch.
  useEffect(() => {
    if (!onDraftPresentationChange) return;
    if (state.status === "ready" && soleRun) {
      onDraftPresentationChange({ runId: soleRun.id, state: getDraftPresentationState(state.progress) });
    } else {
      onDraftPresentationChange(null);
    }
  }, [state, soleRun, onDraftPresentationChange]);

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
  const presentation = getDraftPresentationState(progress);
  const failed = presentation === "FAILED";
  const completed = presentation === "DRAFT_GENERATED";

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
        <p role="alert">{buildFailureCopy(progress)}</p>
      ) : completed ? (
        <p className="muted small-text">{SUCCESS_COPY}</p>
      ) : (
        <p className="muted small-text">Estado de la ejecución: {campaignRunStatusLabel(progress.run_status)}.</p>
      )}
    </section>
  );
}
