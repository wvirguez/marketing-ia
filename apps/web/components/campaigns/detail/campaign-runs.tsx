import { Icon } from "@/components/ui/icon";
import { campaignRunStatusLabel, campaignRunStatusTone, formatCampaignDate } from "@/lib/campaigns/status";
import type { CampaignRunPublic } from "@/types/campaign";

export function CampaignRuns({ runs, total }: { runs: CampaignRunPublic[]; total: number }) {
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
          {runs.map((run) => (
            <article className="panel deliverable-card" key={run.id}>
              <span className="deliverable-icon"><Icon name="campaign" size={19} /></span>
              <div>
                <h3>Ejecución #{run.run_number}</h3>
                <span className={`status ${campaignRunStatusTone(run.status)}`}><span />{campaignRunStatusLabel(run.status)}</span>
                <p className="muted small-text" style={{ marginTop: 6 }}>Creada {formatCampaignDate(run.created_at)}</p>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
