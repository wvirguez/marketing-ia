"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { listCampaigns } from "@/lib/api/campaigns";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { campaignStatusLabel, campaignStatusTone, formatCampaignDate } from "@/lib/campaigns/status";
import { Icon, type IconName } from "@/components/ui/icon";
import { PreviewButton } from "@/components/ui/preview-button";
import { useAuth } from "@/lib/auth/auth-context";
import type { CampaignPublic } from "@/types/campaign";

const RECENT_CAMPAIGNS_LIMIT = 5;

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; items: CampaignPublic[]; total: number };

export function Dashboard() {
  const auth = useAuth();
  const firstName = (auth.status === "authenticated" ? auth.session.user.display_name : "").split(/\s+/)[0] || "";
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    let active = true;
    listCampaigns({ limit: RECENT_CAMPAIGNS_LIMIT })
      .then((response) => {
        if (active) setState({ status: "ready", items: response.items, total: response.total });
      })
      .catch((error) => {
        if (active) setState({ status: "error", message: describeCampaignError(error) });
      });
    return () => {
      active = false;
    };
  }, []);

  return <div className="dashboard">
    <div className="page-heading"><div><div className="eyebrow"><span /> TU CENTRO DE CRECIMIENTO</div><h1>Buenas tardes{firstName ? `, ${firstName}` : ""} <span className="greeting-spark" aria-hidden="true">✦</span></h1><p>Gestiona tus campañas y contenidos con inteligencia artificial.</p></div><Link href="/campaigns/new" className="button primary"><Icon name="plus" size={18} />Nueva campaña</Link></div>
    <section className="hero" aria-labelledby="hero-title"><div className="hero-copy"><span className="hero-label"><Icon name="spark" size={16} /> GRANDES IDEAS. NUEVAS POSIBILIDADES.</span><h2 id="hero-title">Tu próxima campaña<br />empieza con una idea.</h2><p>Tú pones la visión. La inteligencia artificial te ayuda<br className="desktop-break" /> a convertirla en una estrategia con impacto.</p><Link href="/campaigns/new" className="button hero-button">Dale vida a tu idea <Icon name="arrow" size={17} /></Link></div><div className="hero-art" aria-hidden="true"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="orbit orbit-three" /><div className="art-core"><Icon name="spark" size={49} /></div><div className="floating-card strategy"><span className="art-icon"><Icon name="target" /></span><div><strong>Estrategia con propósito</strong><span>Una dirección clara para crecer</span></div><span className="tiny-check">✓</span></div><div className="floating-card idea"><span className="art-icon"><Icon name="content" /></span><div><strong>Ideas que conectan</strong><span>Contenido con tu esencia</span></div></div><span className="art-star star-one">✦</span><span className="art-star star-two">✧</span><span className="art-dot" /></div></section>
    <section aria-labelledby="overview-title"><div className="section-heading"><h2 id="overview-title">Tu actividad, de un vistazo</h2></div><div className="stats-grid single-stat">
      <article className="stat-card"><div className="stat-top"><span>Campañas totales</span><span className="icon-tile blue"><Icon name="campaign" size={19} /></span></div><strong className="stat-value">{state.status === "ready" ? state.total : "—"}</strong></article>
    </div></section>
    <section className="panel campaigns" aria-labelledby="campaigns-title"><div className="panel-heading"><div><h2 id="campaigns-title">Campañas recientes</h2><p>De una buena idea a grandes resultados.</p></div><Link href="/campaigns" className="text-button">Ver todas <Icon name="arrow" size={15} /></Link></div>
      {state.status === "loading" && <p className="muted small-text" role="status">Cargando campañas…</p>}
      {state.status === "error" && <div className="workspace-empty"><span className="workspace-empty-symbol"><Icon name="campaign" size={32} /></span><h2>No pudimos cargar tus campañas.</h2><p>{state.message}</p></div>}
      {state.status === "ready" && state.items.length === 0 && <div className="workspace-empty"><span className="workspace-empty-symbol"><Icon name="campaign" size={32} /></span><h2>Aún no tienes campañas.</h2><p>Crea tu primera campaña para empezar a construir tu próxima idea con Impulso.</p><Link href="/campaigns/new" className="button primary"><Icon name="plus" size={18} />Nueva campaña</Link></div>}
      {state.status === "ready" && state.items.length > 0 && <div className="campaign-list">{state.items.map((campaign) => <Link href={`/campaigns/${campaign.id}`} className="campaign-row" key={campaign.id}><div className="campaign-main"><span className="campaign-avatar blue">{campaign.name.slice(0, 2).toUpperCase()}</span><div><h3>{campaign.name}</h3></div><span className="icon-button campaign-action" aria-label={`Abrir ${campaign.name}`}><Icon name="chevron" size={17} /></span></div><div className="campaign-details"><span className={`status ${campaignStatusTone(campaign.status)}`}><span />{campaignStatusLabel(campaign.status)}</span><time dateTime={campaign.created_at}>{formatCampaignDate(campaign.created_at)}</time></div></Link>)}</div>}
    </section>
    <section aria-labelledby="quick-title"><div className="section-heading"><h2 id="quick-title">¿Qué quieres hacer hoy?</h2><span className="muted small-text">Tu siguiente paso, a un clic</span></div><div className="quick-grid">{([{ title: "Nueva campaña", text: "Dale forma a tu próxima idea", icon: "plus", tone: "blue" }, { title: "Crear contenido", text: "Conecta con tu audiencia", icon: "content", tone: "violet" }, { title: "Ver métricas", text: "Descubre qué está funcionando", icon: "chart", tone: "mint" }, { title: "Planificar calendario", text: "Organiza lo que viene", icon: "calendar", tone: "orange" }] satisfies { title: string; text: string; icon: IconName; tone: string }[]).map(action => action.title === "Nueva campaña" ? <Link href="/campaigns/new" className="quick-card" key={action.title}><span className={`icon-tile ${action.tone}`}><Icon name={action.icon} /></span><span><strong>{action.title}</strong><span>{action.text}</span></span><Icon name="chevron" size={15} /></Link> : <PreviewButton className="quick-card" key={action.title}><span className={`icon-tile ${action.tone}`}><Icon name={action.icon} /></span><span><strong>{action.title}</strong><span>{action.text}</span></span><Icon name="chevron" size={15} /></PreviewButton>)}</div></section>
    <footer className="dashboard-footer"><span>Hecho para ideas que merecen crecer.</span><span>Impulso <span aria-hidden="true">✦</span> Tu creatividad, más lejos.</span></footer>
  </div>;
}
