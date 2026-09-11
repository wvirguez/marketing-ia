"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";
import { PreviewButton } from "@/components/ui/preview-button";
import { listCampaigns } from "@/lib/api/campaigns";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { campaignStatusLabel, campaignStatusTone, formatCampaignDate } from "@/lib/campaigns/status";
import type { CampaignPublic } from "@/types/campaign";

const PAGE_LIMIT = 100;

// Real backend statuses (apps/api/app/campaigns/models.py::CampaignStatus).
// BACKEND-05 only ever persists SUBMITTED today, but the filter covers the
// full canonical set so it stays correct once orchestration moves a
// Campaign further.
const STATUS_OPTIONS = [
  "DRAFT", "SUBMITTED", "ORCHESTRATING", "AWAITING_HUMAN_INPUT", "READY_FOR_EXECUTION",
  "ACTIVE", "PAUSED", "COMPLETED", "ARCHIVED", "CANCELLED",
] as const;

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; items: CampaignPublic[]; total: number };

function CampaignActions({ campaign }: { campaign: CampaignPublic }) {
  const details = useRef<HTMLDetailsElement>(null);
  return <div className="campaign-directory-actions">
    <Link className="button secondary" href={`/campaigns/${campaign.id}`} aria-label={`Ver campaña: ${campaign.name}`}>Ver campaña <Icon name="arrow" size={15} /></Link>
    <details ref={details} className="campaign-directory-menu" onKeyDown={event => { if (event.key === "Escape" && details.current?.open) { event.preventDefault(); details.current.open = false; details.current.querySelector("summary")?.focus(); } }}>
      <summary aria-label={`Más acciones: ${campaign.name}`}>Más acciones</summary>
      {/* Real duplicate/archive actions are not wired in this phase — these
          are genuinely disabled (aria-disabled, no handler), matching the
          codebase's established PreviewButton convention, never an enabled
          no-op that pretends to work. */}
      <div><PreviewButton>Duplicar</PreviewButton><PreviewButton>Archivar</PreviewButton></div>
    </details>
  </div>;
}

const normalize = (value: string) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es").trim();

export function CampaignsList() {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [view, setView] = useState<"list" | "grid">("list");
  const search = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let active = true;
    listCampaigns({ limit: PAGE_LIMIT })
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

  const allItems = state.status === "ready" ? state.items : [];
  const filtered = allItems.filter((campaign) => {
    const matchesStatus = !status || campaign.status === status;
    const matchesQuery = !query || normalize(campaign.name).includes(normalize(query));
    return matchesStatus && matchesQuery;
  });
  const hasActiveFilter = !!(query || status);
  function clear() { setQuery(""); setStatus(""); search.current?.focus(); }

  // `total` is a real backend COUNT(*), independent of the page size, so
  // it is always truthful. The active count is only computed from
  // `allItems` (the fetched page, up to PAGE_LIMIT) — truthful as a
  // global count only when that page actually contains every campaign
  // (allItems.length === total). Otherwise the label makes the bounded
  // scope explicit rather than presenting a partial count as the total.
  const allCampaignsLoaded = state.status === "ready" && allItems.length === state.total;
  const summaries = [
    { label: "Campañas totales", value: state.status === "ready" ? state.total : "—", tone: "blue" },
    {
      label: allCampaignsLoaded ? "Activas" : `Activas (de las primeras ${PAGE_LIMIT})`,
      value: state.status === "ready" ? allItems.filter((item) => item.status === "ACTIVE").length : "—",
      tone: "mint",
    },
  ];

  return <div className="dashboard campaign-directory">
    <header className="page-heading"><div><div className="eyebrow"><span /> TU ESPACIO DE CAMPAÑAS</div><h1>Campañas</h1><p>Organiza y revisa todas tus campañas desde un solo lugar.</p></div><Link className="button primary" href="/campaigns/new"><Icon name="plus" size={18} />Nueva campaña</Link></header>
    <section className="stats-grid" aria-label="Resumen de campañas">{summaries.map(item => <article className="stat-card" key={item.label}><div className="stat-top"><h2>{item.label}</h2><span className={`icon-tile ${item.tone}`}><Icon name="campaign" size={19} /></span></div><strong className="stat-value">{item.value}</strong></article>)}</section>

    {state.status === "loading" && <p className="muted small-text" role="status">Cargando campañas…</p>}

    {state.status === "error" && <section className="panel workspace-empty"><span className="workspace-empty-symbol"><Icon name="campaign" size={32} /></span><h2>No pudimos cargar tus campañas.</h2><p>{state.message}</p></section>}

    {state.status === "ready" && allItems.length === 0 && <section className="panel workspace-empty"><span className="workspace-empty-symbol"><Icon name="campaign" size={32} /></span><h2>Aún no tienes campañas.</h2><p>Crea tu primera campaña para empezar a construir tu próxima idea con Impulso.</p><Link href="/campaigns/new" className="button primary"><Icon name="plus" size={18} />Nueva campaña</Link></section>}

    {state.status === "ready" && allItems.length > 0 && <>
      <section className="panel campaign-directory-tools" aria-label="Buscar y filtrar campañas">
        <div className="campaign-directory-search"><label htmlFor="campaign-search">Buscar campañas</label><input ref={search} id="campaign-search" type="search" placeholder="Nombre de la campaña" value={query} onChange={event => setQuery(event.target.value)} /></div>
        <div className="campaign-directory-filters"><div><label htmlFor="campaign-filter-status">Estado</label><select id="campaign-filter-status" value={status} onChange={event => setStatus(event.target.value)}><option value="">Todas</option>{STATUS_OPTIONS.map(option => <option key={option} value={option}>{campaignStatusLabel(option)}</option>)}</select></div></div>
      </section>
      <div className="campaign-directory-toolbar"><p role="status" aria-live="polite">{filtered.length} de {allItems.length} campañas</p><div className="campaign-directory-view" role="group" aria-label="Vista de campañas"><button type="button" aria-pressed={view === "list"} onClick={() => setView("list")}>Lista</button><button type="button" aria-pressed={view === "grid"} onClick={() => setView("grid")}>Tarjetas</button></div>{hasActiveFilter && filtered.length > 0 && <button className="text-button" type="button" onClick={clear}>Limpiar filtros</button>}</div>
      {filtered.length ? <ul className={`campaign-directory-results view-${view}`} aria-label="Campañas">{filtered.map(campaign => <li className="panel campaign-directory-card" key={campaign.id}>
        <div className="campaign-directory-title"><span className="icon-tile blue"><Icon name="campaign" /></span><div><h2>{campaign.name}</h2></div></div>
        <dl className="campaign-directory-details"><div><dt>Estado</dt><dd><span className={`campaign-directory-badge ${campaignStatusTone(campaign.status)}`}>{campaignStatusLabel(campaign.status)}</span></dd></div><div><dt>Creada</dt><dd><time dateTime={campaign.created_at}>{formatCampaignDate(campaign.created_at)}</time></dd></div></dl>
        <CampaignActions campaign={campaign} />
      </li>)}</ul> : <section className="panel workspace-empty"><span className="workspace-empty-symbol"><Icon name="search" size={32} /></span><h2>No encontramos campañas con esos filtros.</h2><p>Prueba otro nombre o estado, o vuelve a ver todas las campañas.</p><button className="button primary" type="button" onClick={clear}>Limpiar filtros</button></section>}
    </>}
  </div>;
}
