"use client";

// MVP-22: Campaign-wide Distribution Evidence Rollup UI. Rendered as a
// sibling of MetricsPanel/AnalysisPanel inside the same "Métricas" tab —
// never a new tab, never merged into either sibling.
//
// CORE SEMANTIC (frozen, MVP-22A §E): this panel may only ever say "this
// is reported Distribution Evidence, across this Campaign," never that
// any Distribution/Content performed well, caused a result, or won
// against another. No attribution wording, no comparison, no ranking
// anywhere in this file.
//
// Purely read-only: fetches the server-computed rollup and renders it
// exactly as returned — no client-side recomputation of report_count, no
// choosing latest/earliest, no metric_name normalization, no independent
// sort. A fetch failure stays local to this panel; it never hides or
// blocks MetricsPanel/AnalysisPanel or any other Campaign Workspace data.

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { getCampaignDistributionEvidenceRollup } from "@/lib/api/measurement";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { formatCampaignDate } from "@/lib/campaigns/status";
import type { CampaignDistributionEvidenceRollupPublic } from "@/types/measurement";

type RollupState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: CampaignDistributionEvidenceRollupPublic };

const TITLE = "Resumen de evidencia reportada de la campaña";
const SUBTITLE = "Registro descriptivo de evidencia reportada en todas las distribuciones; sin atribución causal.";
const EMPTY_COPY = "Aún no hay evidencia reportada para esta campaña.";

export function CampaignDistributionEvidenceRollupPanel({
  campaignId,
  active,
  refreshToken,
}: {
  campaignId: string;
  active: boolean;
  refreshToken: number;
}) {
  const [state, setState] = useState<RollupState>({ status: "loading" });
  const requestedTokenRef = useRef<number | null>(null);

  function load(): Promise<CampaignDistributionEvidenceRollupPublic> {
    return getCampaignDistributionEvidenceRollup(campaignId);
  }

  function retry() {
    setState({ status: "loading" });
    load()
      .then((data) => setState({ status: "ready", data }))
      .catch((error) => setState({ status: "error", message: describeCampaignError(error) }));
  }

  useEffect(() => {
    if (!active || requestedTokenRef.current === refreshToken) return;
    let cancelled = false;
    requestedTokenRef.current = refreshToken;
    load()
      .then((data) => {
        if (!cancelled) setState({ status: "ready", data });
      })
      .catch((error) => {
        if (!cancelled) {
          requestedTokenRef.current = null;
          setState({ status: "error", message: describeCampaignError(error) });
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, campaignId, refreshToken]);

  return (
    <section className="panel" style={{ marginTop: 16 }}>
      <div className="section-heading">
        <h3>{TITLE}</h3>
      </div>
      <p className="muted small-text">{SUBTITLE}</p>

      {state.status === "loading" && (
        <p className="muted small-text" role="status">
          Cargando resumen de la campaña…
        </p>
      )}

      {state.status === "error" && (
        <p className="small-text" role="alert">
          {state.message}{" "}
          <button type="button" className="auth-text-button" onClick={retry}>
            Reintentar
          </button>
        </p>
      )}

      {state.status === "ready" &&
        (state.data.metrics.length === 0 ? (
          <p className="muted small-text">{EMPTY_COPY}</p>
        ) : (
          <div className="deliverables-grid">
            {state.data.metrics.map((metric) => (
              <article key={metric.metric_name} className="panel deliverable-card">
                <span className="deliverable-icon">
                  <Icon name="chart" size={19} />
                </span>
                <div>
                  <h3>{metric.metric_name}</h3>
                  <p className="muted small-text" style={{ marginTop: 8 }}>
                    {metric.report_count} reporte(s) actual(es) en la campaña.
                  </p>
                  <dl style={{ margin: "10px 0 0" }}>
                    <div>
                      <dt className="muted small-text">Último valor reportado</dt>
                      <dd style={{ margin: 0 }}>
                        {metric.latest.value} ({metric.latest.period_start} – {metric.latest.period_end})
                      </dd>
                      <dd className="muted small-text" style={{ margin: 0 }}>
                        Reportado el {formatCampaignDate(metric.latest.reported_at)}, canal {metric.latest.channel}
                      </dd>
                    </div>
                    <div style={{ marginTop: 8 }}>
                      <dt className="muted small-text">Primer valor reportado</dt>
                      <dd style={{ margin: 0 }}>
                        {metric.earliest.value} ({metric.earliest.period_start} – {metric.earliest.period_end})
                      </dd>
                      <dd className="muted small-text" style={{ margin: 0 }}>
                        Reportado el {formatCampaignDate(metric.earliest.reported_at)}, canal {metric.earliest.channel}
                      </dd>
                    </div>
                  </dl>
                </div>
              </article>
            ))}
          </div>
        ))}
    </section>
  );
}
