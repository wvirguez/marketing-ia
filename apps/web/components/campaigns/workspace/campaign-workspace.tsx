"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import { campaignDemo, workspaceTabs } from "@/lib/campaign-demo-data";
import type { WorkspaceTab } from "@/types/campaign-workspace";
import { Icon } from "@/components/ui/icon";
import { CampaignOverview } from "./campaign-overview";
import { WorkspacePanel } from "./workspace-panels";
import { WorkspaceStatus } from "./workspace-status";

export function CampaignWorkspace() {
  const [active, setActive] = useState<WorkspaceTab>("overview");
  const buttons = useRef<Partial<Record<WorkspaceTab, HTMLButtonElement | null>>>({});
  function selectTab(tab: WorkspaceTab, focus = false) {
    setActive(tab);
    if (focus) {
      buttons.current[tab]?.focus({ preventScroll: true });
      buttons.current[tab]?.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }
  function handleKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = event.key === "ArrowRight" ? (index + 1) % workspaceTabs.length : event.key === "ArrowLeft" ? (index - 1 + workspaceTabs.length) % workspaceTabs.length : event.key === "Home" ? 0 : event.key === "End" ? workspaceTabs.length - 1 : null;
    if (next !== null) { event.preventDefault(); selectTab(workspaceTabs[next].id, true); }
  }
  return <div className="dashboard campaign-workspace">
    <header className="workspace-header"><div className="workspace-title-row"><span className="workspace-campaign-icon"><Icon name="campaign" size={27} /></span><div><div className="eyebrow"><span /> CAMPAIGN WORKSPACE</div><h1>{campaignDemo.name}</h1></div><WorkspaceStatus value={campaignDemo.status} /></div><p className="workspace-description">{campaignDemo.description}</p><dl className="workspace-metadata">{[{ label: "Producto", value: campaignDemo.product }, { label: "Precio", value: campaignDemo.price }, { label: "Canal inicial", value: campaignDemo.channel }, { label: "Estado", value: campaignDemo.status }].map(item => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl></header>
    <div className="workspace-demo-notice"><span className="demo-pill">DATOS DE DEMOSTRACIÓN</span><p>Estás viendo una campaña de ejemplo fija. No fue generada a partir de tu instrucción y no está en ejecución.</p></div>
    <div className="workspace-tabs" role="tablist" aria-label="Secciones de la campaña">{workspaceTabs.map((tab, index) => <button type="button" role="tab" key={tab.id} id={`tab-${tab.id}`} aria-selected={active === tab.id} aria-controls={`panel-${tab.id}`} tabIndex={active === tab.id ? 0 : -1} ref={element => { buttons.current[tab.id] = element; }} onClick={() => selectTab(tab.id)} onKeyDown={event => handleKey(event, index)}>{tab.label}</button>)}</div>
    {workspaceTabs.map(tab => <section key={tab.id} role="tabpanel" id={`panel-${tab.id}`} aria-labelledby={`tab-${tab.id}`} tabIndex={0} hidden={active !== tab.id} className="workspace-tab-panel">{tab.id === "overview" ? <CampaignOverview onNavigate={id => selectTab(id, true)} /> : <WorkspacePanel tab={tab.id} />}</section>)}
    <footer className="dashboard-footer"><span>Una visión clara. Todo lo necesario, en un solo espacio.</span><span>Impulso <span aria-hidden="true">✦</span> Tu creatividad, más lejos.</span></footer>
  </div>;
}
