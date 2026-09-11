import { Icon } from "@/components/ui/icon";
import type { DraftPresentationState } from "@/lib/campaigns/draft-progress";
import { campaignRunStatusLabel, campaignRunStatusTone, formatCampaignDate } from "@/lib/campaigns/status";
import type { CampaignRunPublic } from "@/types/campaign";

// MVP-06B: overrides the raw run-status badge only for the specific run
// `GenerateDraftAction` has actually computed a presentation state for,
// and only once that state is resolved (never for NOT_STARTED, which
// already reads correctly as the raw "Creada" label) — this is the fix
// for a real, observed contradiction: a CampaignRun that is technically
// still `RUNNING` at the backend (RUNNING != actively executing) once its
// deterministic bootstrap has actually completed through CONTENT was
// rendered here as "En ejecución," directly beside GenerateDraftAction's
// own truthful "Borrador inicial generado." `campaignRunStatusLabel`/
// `Tone` themselves are untouched — they keep mapping the raw
// CampaignRunStatus domain exactly as before for every other case.
const DRAFT_PRESENTATION_LABELS: Partial<Record<DraftPresentationState, string>> = {
  GENERATING: "Generando borrador…",
  DRAFT_GENERATED: "Borrador generado",
  FAILED: "Borrador incompleto",
};

const DRAFT_PRESENTATION_TONES: Partial<Record<DraftPresentationState, string>> = {
  GENERATING: "warning",
  DRAFT_GENERATED: "success",
  FAILED: "warning",
};

export function CampaignRuns({
  runs,
  total,
  draftPresentation,
}: {
  runs: CampaignRunPublic[];
  total: number;
  /** MVP-06B: the sole run's derived bootstrap presentation, reported by
   * `GenerateDraftAction` — `null`/`undefined` (or a non-matching
   * `runId`) simply falls back to the raw status, so this prop is
   * optional and safe to omit entirely. */
  draftPresentation?: { runId: string; state: DraftPresentationState } | null;
}) {
  return (
    <section aria-labelledby="runs-title">
      <div className="section-heading"><h2 id="runs-title">Ejecuciones <span className="workspace-count">{total}</span></h2><span className="muted small-text">Estado persistido de esta campaña</span></div>
      {runs.length === 0 ? (
        <section className="panel workspace-empty">
          <span className="workspace-empty-symbol"><Icon name="campaign" size={32} /></span>
          <h2>Todavía no hay ejecuciones registradas.</h2>
          <p>Cuando exista una ejecución para esta campaña, aparecerá aquí con su estado real.</p>
        </section>
      ) : (
        <div className="deliverables-grid">
          {runs.map((run) => {
            const override =
              draftPresentation && draftPresentation.runId === run.id && draftPresentation.state !== "NOT_STARTED"
                ? draftPresentation.state
                : null;
            const label = (override && DRAFT_PRESENTATION_LABELS[override]) || campaignRunStatusLabel(run.status);
            const tone = (override && DRAFT_PRESENTATION_TONES[override]) || campaignRunStatusTone(run.status);
            return (
              <article className="panel deliverable-card" key={run.id}>
                <span className="deliverable-icon"><Icon name="campaign" size={19} /></span>
                <div>
                  <h3>Ejecución #{run.run_number}</h3>
                  <span className={`status ${tone}`}><span />{label}</span>
                  <p className="muted small-text" style={{ marginTop: 6 }}>Creada {formatCampaignDate(run.created_at)}</p>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
