"use client";

import Link from "next/link";
import { useRef, useState, type ReactNode } from "react";
import { Icon, type IconName } from "@/components/ui/icon";
import { PreviewButton } from "@/components/ui/preview-button";

const navigation: { label: string; icon: IconName }[] = [
  { label: "Dashboard", icon: "grid" }, { label: "Campañas", icon: "campaign" },
  { label: "Contenido", icon: "content" }, { label: "Creativos", icon: "image" },
  { label: "Calendario", icon: "calendar" }, { label: "Paid Media", icon: "target" },
  { label: "Métricas", icon: "chart" },
];
export function AppShell({ children, section = "dashboard" }: { children: ReactNode; section?: "dashboard" | "campaigns" }) {
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  return <div className="app-shell" onKeyDown={(event) => {
    if (event.key === "Escape" && open) { setOpen(false); toggle.current?.focus(); }
  }}>
    <a href="#main-content" className="skip-link">Saltar al contenido</a>
    <div className="mobile-header"><Link href="/" className="brand"><span className="brand-symbol"><Icon name="spark" size={24} /></span>impulso<span className="brand-dot">.</span></Link><button ref={toggle} type="button" className="icon-button" aria-label={open ? "Cerrar navegación" : "Abrir navegación"} aria-expanded={open} aria-controls="sidebar" onClick={() => setOpen(!open)}><Icon name="menu" /></button></div>
    <aside id="sidebar" className={`sidebar ${open ? "is-open" : ""}`}>
      <Link className="brand desktop-brand" href="/" aria-label="Impulso, inicio"><span className="brand-symbol"><Icon name="spark" size={25} /></span>impulso<span className="brand-dot">.</span></Link>
      <div className="workspace"><span className="workspace-icon">E</span><div><strong>Emilia Studio</strong><span>Espacio de trabajo</span></div><span className="workspace-chevron">⌄</span></div>
      <p className="nav-caption">WORKSPACE</p>
      <nav aria-label="Navegación principal"><ul className="nav-list">{navigation.map(({ label, icon }, index) => {
        const active = (index === 0 && section === "dashboard") || (index === 1 && section === "campaigns");
        return <li key={label}>{index < 2 ? <Link href={index === 0 ? "/" : "/campaigns/new"} aria-current={active ? "page" : undefined} className={`nav-item${active ? " active" : ""}`} onClick={() => setOpen(false)}><Icon name={icon} />{label}{active ? <span className="active-dot" /> : index === 1 && <span className="nav-count">8</span>}</Link> : <PreviewButton className="nav-item"><Icon name={icon} />{label}</PreviewButton>}</li>;
      })}</ul></nav>
      <div className="sidebar-bottom"><div className="plan-card"><span className="plan-label"><Icon name="spark" size={16} /> Tu próxima gran idea</span><p>De la inspiración al impacto, en un solo lugar.</p><span className="plan-badge">Plan Studio · Demo</span></div><PreviewButton className="nav-item"><Icon name="settings" />Configuración</PreviewButton><div className="user-profile"><span className="avatar">EM</span><div><strong>Emilia Martínez</strong><span>Plan Studio · Demo</span></div></div></div>
    </aside>
    <div className="app-body"><header className="topbar"><nav className="breadcrumb" aria-label="Ruta de navegación"><span>{section === "campaigns" ? "Campañas" : "Workspace"}</span><Icon name="chevron" size={13} /><strong aria-current="page">{section === "campaigns" ? "Nueva campaña" : "Dashboard"}</strong></nav><div className="topbar-tools"><label className="search"><Icon name="search" size={18} /><span className="sr-only">Buscar, disponible próximamente</span><input type="search" placeholder="Buscar en tu espacio..." disabled /></label><PreviewButton className="icon-button notification" aria-label="Notificaciones, próximamente"><Icon name="bell" /><span /></PreviewButton><span className="topbar-divider" /><span className="avatar small" aria-label="Emilia Martínez">EM</span></div></header><main id="main-content" tabIndex={-1}>{children}</main></div>
  </div>;
}
