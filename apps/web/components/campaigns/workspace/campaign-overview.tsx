import { campaignDemo } from "@/lib/campaign-demo-data";
import type { WorkspaceTab } from "@/types/campaign-workspace";
import { Icon, type IconName } from "@/components/ui/icon";
import { WorkspaceStatus } from "./workspace-status";

const highlightIcons: IconName[] = ["target", "campaign", "content", "spark"];
export function CampaignOverview({ onNavigate }: { onNavigate: (tab: WorkspaceTab) => void }) {
  return <>
    <div className="workspace-overview-grid">
      <section className="workspace-progress-card" aria-labelledby="campaign-state-title">
        <span className="workspace-eyebrow"><Icon name="spark" size={16} /> CADA PASO CUENTA</span>
        <h2 id="campaign-state-title">Tu campaña está<br />en preparación</h2>
        <p>La dirección está definida. Ahora es momento de dar forma a tus contenidos.</p>
        <div className="workspace-progress-value"><strong>{campaignDemo.progress}%</strong><span>del recorrido de ejemplo</span></div>
        <progress value={campaignDemo.progress} max={100} aria-label="Progreso mock de campaña" />
        <span className="workspace-next-step">Siguiente paso <Icon name="arrow" size={14} /> Contenido en producción</span>
      </section>
      <section className="panel workspace-workflow" aria-labelledby="workflow-title"><div className="workspace-section-heading"><h2 id="workflow-title">Así avanza tu campaña</h2><span className="example-tag">Simulación</span></div>
        <ol>{campaignDemo.workflow.map(step => <li key={step.label} className={step.state}><span className="activity-marker" aria-hidden="true">{step.state === "done" ? <Icon name="check" size={13} /> : step.state === "current" ? "●" : "○"}</span><span>{step.label}<span className="sr-only">: {step.state === "done" ? "completado" : step.state === "current" ? "en curso" : "pendiente"}</span></span></li>)}</ol>
      </section>
    </div>
    <section aria-labelledby="essentials-title"><div className="section-heading"><h2 id="essentials-title">La esencia de tu campaña</h2><span className="muted small-text">Una dirección compartida</span></div><div className="workspace-highlights">{campaignDemo.highlights.map((item, index) => <article className="panel workspace-highlight" key={item.label}><span className={`icon-tile ${["blue", "mint", "violet", "orange"][index]}`}><Icon name={highlightIcons[index]} size={18} /></span><h3>{item.label}</h3><p>{item.value}</p></article>)}</div></section>
    <section aria-labelledby="deliverables-title"><div className="section-heading"><h2 id="deliverables-title">Entregables <span className="workspace-count">9</span></h2><span className="muted small-text">Todo en su lugar</span></div><div className="deliverables-grid">{campaignDemo.deliverables.map(item => <article className="panel deliverable-card" key={item.title}><span className="deliverable-icon"><Icon name={item.tab === "creatives" ? "image" : "content"} size={19} /></span><div><h3>{item.title}</h3><WorkspaceStatus value={item.status} /></div><button type="button" onClick={() => onNavigate(item.tab)} className="text-button" aria-label={`Ver ${item.title}`}>Ver <Icon name="arrow" size={14} /></button></article>)}</div></section>
  </>;
}
