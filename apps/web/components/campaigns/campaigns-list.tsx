"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { campaignChannels, campaignDate, campaignList, campaignStatuses, campaignStatusTone, campaignTypes, filterCampaigns, type CampaignListItem } from "@/lib/campaign-list-data";

function CampaignActions({ campaign }: { campaign: CampaignListItem }) {
  const [feedback, setFeedback] = useState({ message: "", sequence: 0 });
  function inform(message: string) { setFeedback(previous => ({ message, sequence: previous.sequence + 1 })); }
  const details = useRef<HTMLDetailsElement>(null);
  function action(label: string) {
    inform(`${campaign.name} · ${label}: Esta acción estará disponible cuando conectemos el backend.`);
    if (details.current) { details.current.open = false; details.current.querySelector("summary")?.focus(); }
  }
  return <div className="campaign-directory-actions">
    {campaign.workspaceHref ? <Link className="button secondary" href={campaign.workspaceHref} aria-label={`Ver campaña: ${campaign.name}`}>Ver campaña <Icon name="arrow" size={15} /></Link> : <button className="button secondary" type="button" aria-label={`Ver campaña: ${campaign.name}`} onClick={() => inform(`${campaign.name}: Esta campaña es de demostración y todavía no tiene un Workspace disponible.`)}>Ver campaña</button>}
    <details ref={details} className="campaign-directory-menu" onKeyDown={event => { if (event.key === "Escape" && details.current?.open) { event.preventDefault(); details.current.open = false; details.current.querySelector("summary")?.focus(); } }}>
      <summary aria-label={`Más acciones: ${campaign.name}`}>Más acciones</summary>
      <div><button type="button" onClick={() => action("Duplicar")}>Duplicar</button><button type="button" onClick={() => action("Archivar")}>Archivar</button></div>
    </details>
    <div role="status" aria-live="polite" aria-atomic="true" className={`campaign-directory-feedback${feedback.message ? " is-visible" : ""}`}>{feedback.message && <p key={feedback.sequence}>{feedback.message}</p>}</div>
  </div>;
}

export function CampaignsList() {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [productType, setProductType] = useState("");
  const [channel, setChannel] = useState("");
  const [view, setView] = useState<"list" | "grid">("list");
  const search = useRef<HTMLInputElement>(null);
  const results = filterCampaigns(query, status, productType, channel);
  const filtered = !!(query || status || productType || channel);
  function clear() { setQuery(""); setStatus(""); setProductType(""); setChannel(""); search.current?.focus(); }
  const summaries = [
    { label: "Campañas totales", value: campaignList.length, tone: "blue" },
    { label: "Activas", value: campaignList.filter(item => item.status === "Activa").length, tone: "mint" },
    { label: "En preparación", value: campaignList.filter(item => item.status === "En preparación").length, tone: "orange" },
    { label: "Completadas", value: campaignList.filter(item => item.status === "Completada").length, tone: "violet" },
  ];
  return <div className="dashboard campaign-directory">
    <header className="page-heading"><div><div className="eyebrow"><span /> TU ESPACIO DE CAMPAÑAS</div><h1>Campañas</h1><p>Organiza y revisa todas tus campañas desde un solo lugar.</p></div><Link className="button primary" href="/campaigns/new"><Icon name="plus" size={18} />Nueva campaña</Link></header>
    <div className="workspace-demo-notice"><span className="demo-pill">Datos de demostración</span><p>Los estados y el progreso son ficticios. No representan campañas en ejecución, publicaciones ni anuncios reales.</p></div>
    <section className="stats-grid" aria-label="Resumen de campañas de demostración">{summaries.map(item => <article className="stat-card" key={item.label}><div className="stat-top"><h2>{item.label}</h2><span className={`icon-tile ${item.tone}`}><Icon name="campaign" size={19} /></span></div><strong className="stat-value">{item.value}</strong></article>)}</section>
    <section className="panel campaign-directory-tools" aria-label="Buscar y filtrar campañas">
      <div className="campaign-directory-search"><label htmlFor="campaign-search">Buscar campañas</label><input ref={search} id="campaign-search" type="search" placeholder="Nombre, tipo o canal" value={query} onChange={event => setQuery(event.target.value)} /></div>
      <div className="campaign-directory-filters">{[{ id: "status", label: "Estado", value: status, set: setStatus, options: campaignStatuses, all: "Todas" }, { id: "type", label: "Tipo", value: productType, set: setProductType, options: campaignTypes, all: "Todos" }, { id: "channel", label: "Canal", value: channel, set: setChannel, options: campaignChannels, all: "Todos" }].map(filter => <div key={filter.id}><label htmlFor={`campaign-filter-${filter.id}`}>{filter.label}</label><select id={`campaign-filter-${filter.id}`} value={filter.value} onChange={event => filter.set(event.target.value)}><option value="">{filter.all}</option>{filter.options.map(option => <option key={option}>{option}</option>)}</select></div>)}</div>
    </section>
    <div className="campaign-directory-toolbar"><p role="status" aria-live="polite">{results.length} de {campaignList.length} campañas</p><div className="campaign-directory-view" role="group" aria-label="Vista de campañas"><button type="button" aria-pressed={view === "list"} onClick={() => setView("list")}>Lista</button><button type="button" aria-pressed={view === "grid"} onClick={() => setView("grid")}>Tarjetas</button></div>{filtered && results.length > 0 && <button className="text-button" type="button" onClick={clear}>Limpiar filtros</button>}</div>
    {results.length ? <ul className={`campaign-directory-results view-${view}`} aria-label="Campañas">{results.map(campaign => <li className="panel campaign-directory-card" key={campaign.id}>
      <div className="campaign-directory-title"><span className="icon-tile blue"><Icon name="campaign" /></span><div><h2>{campaign.name}</h2><p>{campaign.description}</p></div></div>
      <dl className="campaign-directory-details"><div><dt>Tipo</dt><dd>{campaign.productType}</dd></div><div><dt>Canal</dt><dd>{campaign.channel}</dd></div><div><dt>Estado</dt><dd><span className={`campaign-directory-badge ${campaignStatusTone[campaign.status]}`}>{campaign.status}</span></dd></div><div><dt>Progreso de ejemplo</dt><dd className="campaign-directory-progress"><progress max={100} value={campaign.progress} aria-label={`Progreso de ejemplo: ${campaign.name}`} /><span>{campaign.progress}%</span></dd></div><div><dt>Última actualización</dt><dd><time dateTime={campaign.updatedDate}>{campaignDate(campaign.updatedDate)}</time></dd></div></dl>
      <CampaignActions campaign={campaign} />
    </li>)}</ul> : <section className="panel workspace-empty"><span className="workspace-empty-symbol"><Icon name="search" size={32} /></span><h2>No encontramos campañas con esos filtros.</h2><p>Prueba otro nombre, tipo o canal, o vuelve a ver todas las campañas.</p><button className="button primary" type="button" onClick={clear}>Limpiar filtros</button></section>}
  </div>;
}
