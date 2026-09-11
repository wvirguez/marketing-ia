"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Icon, type IconName } from "@/components/ui/icon";
import { getCampaign, listCampaignRuns } from "@/lib/api/campaigns";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { campaignStatusLabel, campaignStatusTone, formatCampaignDate } from "@/lib/campaigns/status";
import type { CampaignPublic, CampaignRunPublic } from "@/types/campaign";
import { AudiencePanel } from "./audience-panel";
import { CampaignRuns } from "./campaign-runs";
import { GenerateDraftAction } from "./generate-draft-action";
import { NotAvailableYetPanel } from "./not-available-yet-panel";
import { PlanPanel } from "./plan-panel";
import { ResearchPanel } from "./research-panel";
import { StrategyPanel } from "./strategy-panel";

const TABS: { id: string; label: string }[] = [
  { id: "overview", label: "Resumen" },
  { id: "research", label: "Investigación" },
  { id: "audience", label: "Audiencia" },
  { id: "strategy", label: "Estrategia" },
  { id: "plan", label: "Plan" },
  { id: "content", label: "Contenido" },
  { id: "creatives", label: "Creatividades" },
  { id: "tracking", label: "Tracking" },
  { id: "metrics", label: "Métricas" },
];

const TAB_UNAVAILABLE_COPY: Record<string, { icon: IconName; title: string; body: string }> = {
  content: { icon: "content", title: "Contenido aún no disponible", body: "Esta sección se conectará en una próxima fase de integración." },
  creatives: { icon: "image", title: "Creatividades aún no disponibles", body: "Esta sección se conectará en una próxima fase de integración." },
  tracking: { icon: "chart", title: "Tracking aún no disponible", body: "Esta sección se conectará en una próxima fase de integración." },
  metrics: { icon: "chart", title: "Métricas aún no disponibles", body: "Esta sección se conectará en una próxima fase de integración." },
};

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; campaign: CampaignPublic; runs: CampaignRunPublic[]; runsTotal: number };

export function CampaignDetail({ campaignId }: { campaignId: string }) {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [active, setActive] = useState("overview");
  // MVP-05B: bumped every time a "Generar borrador" attempt reaches the
  // backend, so ResearchPanel/AudiencePanel know a previously-cached
  // pre-generation (or stale) output must be re-fetched next time their
  // tab is opened.
  const [draftVersion, setDraftVersion] = useState(0);
  const buttons = useRef<Partial<Record<string, HTMLButtonElement | null>>>({});

  useEffect(() => {
    let active = true;
    Promise.all([getCampaign(campaignId), listCampaignRuns(campaignId)])
      .then(([campaign, runsResponse]) => {
        if (active) setState({ status: "ready", campaign, runs: runsResponse.items, runsTotal: runsResponse.total });
      })
      .catch((error) => {
        if (active) setState({ status: "error", message: describeCampaignError(error) });
      });
    return () => {
      active = false;
    };
  }, [campaignId]);

  function selectTab(tab: string, focus = false) {
    setActive(tab);
    if (focus) {
      buttons.current[tab]?.focus({ preventScroll: true });
      buttons.current[tab]?.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }
  function handleKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = event.key === "ArrowRight" ? (index + 1) % TABS.length : event.key === "ArrowLeft" ? (index - 1 + TABS.length) % TABS.length : event.key === "Home" ? 0 : event.key === "End" ? TABS.length - 1 : null;
    if (next !== null) { event.preventDefault(); selectTab(TABS[next].id, true); }
  }

  if (state.status === "loading") {
    return <div className="dashboard campaign-workspace"><p className="muted small-text" role="status">Cargando campaña…</p></div>;
  }

  if (state.status === "error") {
    return <div className="dashboard campaign-workspace"><section className="panel workspace-empty"><span className="workspace-empty-symbol"><Icon name="campaign" size={32} /></span><h2>No pudimos abrir esta campaña.</h2><p>{state.message}</p></section></div>;
  }

  // R3 (MVP-03R): if the URL's campaignId changed, a new fetch for it is
  // already in flight (see the effect above, keyed on campaignId), but
  // `state` may still hold the *previous* campaign's data until that
  // fetch resolves. Rendering purely from a derived check here — no
  // extra setState — means the previous campaign is never shown under
  // the new campaign's URL.
  if (state.campaign.id !== campaignId) {
    return <div className="dashboard campaign-workspace"><p className="muted small-text" role="status">Cargando campaña…</p></div>;
  }

  const { campaign, runs, runsTotal } = state;

  return <div className="dashboard campaign-workspace">
    <header className="workspace-header">
      <div className="workspace-title-row">
        <span className="workspace-campaign-icon"><Icon name="campaign" size={27} /></span>
        <div><div className="eyebrow"><span /> CAMPAÑA</div><h1>{campaign.name}</h1></div>
        <span className={`status ${campaignStatusTone(campaign.status)}`}><span />{campaignStatusLabel(campaign.status)}</span>
      </div>
      <dl className="workspace-metadata">
        <div><dt>Creada</dt><dd>{formatCampaignDate(campaign.created_at)}</dd></div>
        <div><dt>Última actualización</dt><dd>{formatCampaignDate(campaign.updated_at)}</dd></div>
        {campaign.archived_at && <div><dt>Archivada</dt><dd>{formatCampaignDate(campaign.archived_at)}</dd></div>}
      </dl>
    </header>

    <div className="workspace-tabs" role="tablist" aria-label="Secciones de la campaña">{TABS.map((tab, index) => <button type="button" role="tab" key={tab.id} id={`tab-${tab.id}`} aria-selected={active === tab.id} aria-controls={`panel-${tab.id}`} tabIndex={active === tab.id ? 0 : -1} ref={element => { buttons.current[tab.id] = element; }} onClick={() => selectTab(tab.id)} onKeyDown={event => handleKey(event, index)}>{tab.label}</button>)}</div>

    {TABS.map(tab => <section key={tab.id} role="tabpanel" id={`panel-${tab.id}`} aria-labelledby={`tab-${tab.id}`} tabIndex={0} hidden={active !== tab.id} className="workspace-tab-panel">
      {tab.id === "overview" && (
        <>
          <GenerateDraftAction
            key={campaignId}
            campaignId={campaignId}
            runs={runs}
            onGenerated={() => setDraftVersion((version) => version + 1)}
          />
          <CampaignRuns runs={runs} total={runsTotal} />
        </>
      )}
      {tab.id === "research" && (
        <ResearchPanel key={campaignId} campaignId={campaignId} active={active === "research"} refreshToken={draftVersion} />
      )}
      {tab.id === "audience" && (
        <AudiencePanel key={campaignId} campaignId={campaignId} active={active === "audience"} refreshToken={draftVersion} />
      )}
      {tab.id === "strategy" && (
        <StrategyPanel key={campaignId} campaignId={campaignId} active={active === "strategy"} refreshToken={draftVersion} />
      )}
      {tab.id === "plan" && (
        <PlanPanel key={campaignId} campaignId={campaignId} active={active === "plan"} refreshToken={draftVersion} />
      )}
      {tab.id !== "overview" &&
        tab.id !== "research" &&
        tab.id !== "audience" &&
        tab.id !== "strategy" &&
        tab.id !== "plan" && <NotAvailableYetPanel {...TAB_UNAVAILABLE_COPY[tab.id]} />}
    </section>)}

    <footer className="dashboard-footer"><span>Una visión clara. Todo lo necesario, en un solo espacio.</span><span>Impulso <span aria-hidden="true">✦</span> Tu creatividad, más lejos.</span></footer>
  </div>;
}
