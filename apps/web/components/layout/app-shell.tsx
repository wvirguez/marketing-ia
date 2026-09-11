"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Fragment, useRef, useState, type ReactNode } from "react";
import { campaignList } from "@/lib/campaign-list-data";
import { Icon, type IconName } from "@/components/ui/icon";
import { PreviewButton } from "@/components/ui/preview-button";
import { useAuth } from "@/lib/auth/auth-context";
import { describeAuthError } from "@/lib/auth/error-messages";

const navigation: { label: string; icon: IconName }[] = [
  { label: "Dashboard", icon: "grid" }, { label: "Campañas", icon: "campaign" },
  { label: "Contenido", icon: "content" }, { label: "Creativos", icon: "image" },
  { label: "Calendario", icon: "calendar" }, { label: "Paid Media", icon: "target" },
  { label: "Métricas", icon: "chart" },
];
export function AppShell({ children, section = "dashboard", context, breadcrumbs }: { children: ReactNode; section?: "dashboard" | "campaigns" | "settings"; context?: string; breadcrumbs?: string[] }) {
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const auth = useAuth();
  const router = useRouter();
  const session = auth.status === "authenticated" ? auth.session : null;
  const workspaceName = session?.workspace.name ?? "Tu espacio";
  const userName = session?.user.display_name ?? "";
  const initial = (workspaceName[0] ?? "?").toUpperCase();
  const userInitials = userName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "?";
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");

  async function handleLogout() {
    if (loggingOut) return; // prevent duplicate concurrent logout requests
    setLoggingOut(true);
    setLogoutError("");
    try {
      await auth.logout();
      // Only reached when the backend confirmed the session is gone
      // (a real 200, or an authoritative 401 proving it already was) —
      // see lib/auth/auth-context.tsx. Any other failure throws instead
      // and is handled below without ever navigating away.
      router.replace("/login");
    } catch (error) {
      setLogoutError(describeAuthError(error));
      setLoggingOut(false);
    }
  }

  return <div className="app-shell" onKeyDown={(event) => {
    if (event.key === "Escape" && open) { setOpen(false); toggle.current?.focus(); }
  }}>
    <a href="#main-content" className="skip-link">Saltar al contenido</a>
    <div className="mobile-header"><Link href="/" className="brand"><span className="brand-symbol"><Icon name="spark" size={24} /></span>impulso<span className="brand-dot">.</span></Link><button ref={toggle} type="button" className="icon-button" aria-label={open ? "Cerrar navegación" : "Abrir navegación"} aria-expanded={open} aria-controls="sidebar" onClick={() => setOpen(!open)}><Icon name="menu" /></button></div>
    <aside id="sidebar" className={`sidebar ${open ? "is-open" : ""}`}>
      <Link className="brand desktop-brand" href="/" aria-label="Impulso, inicio"><span className="brand-symbol"><Icon name="spark" size={25} /></span>impulso<span className="brand-dot">.</span></Link>
      <div className="workspace"><span className="workspace-icon">{initial}</span><div><strong>{workspaceName}</strong><span>Espacio de trabajo</span></div><span className="workspace-chevron">⌄</span></div>
      <p className="nav-caption">WORKSPACE</p>
      <nav aria-label="Navegación principal"><ul className="nav-list">{navigation.map(({ label, icon }, index) => {
        const active = (index === 0 && section === "dashboard") || (index === 1 && section === "campaigns");
        return <li key={label}>{index < 2 ? <Link href={index === 0 ? "/" : "/campaigns"} aria-current={active ? "page" : undefined} className={`nav-item${active ? " active" : ""}`} onClick={() => setOpen(false)}><Icon name={icon} />{label}{active ? <span className="active-dot" /> : index === 1 && <span className="nav-count">{campaignList.length}</span>}</Link> : <PreviewButton className="nav-item"><Icon name={icon} />{label}</PreviewButton>}</li>;
      })}</ul></nav>
      <div className="sidebar-bottom"><div className="plan-card"><span className="plan-label"><Icon name="spark" size={16} /> Tu próxima gran idea</span><p>De la inspiración al impacto, en un solo lugar.</p><span className="plan-badge">Plan Studio · Demo</span></div><Link href="/settings" aria-current={section === "settings" ? "page" : undefined} className={`nav-item${section === "settings" ? " active" : ""}`} onClick={() => setOpen(false)}><Icon name="settings" />Configuración</Link><div className="user-profile"><span className="avatar">{userInitials}</span><div><strong>{userName}</strong><span>Plan Studio · Demo</span></div><button type="button" className="icon-button" aria-label="Cerrar sesión" title="Cerrar sesión" aria-busy={loggingOut} disabled={loggingOut} onClick={handleLogout}><Icon name="logout" size={17} /></button></div>{logoutError && <p className="logout-error" role="alert">{logoutError} <button type="button" className="auth-text-button" onClick={handleLogout}>Reintentar</button></p>}</div>
    </aside>
    <div className="app-body"><header className={`topbar${context || breadcrumbs ? " workspace-topbar" : ""}`}><nav className={`breadcrumb${breadcrumbs ? " breadcrumb-long" : ""}`} aria-label="Ruta de navegación">{(breadcrumbs ?? [section === "campaigns" ? "Campañas" : "Workspace", context ?? (section === "campaigns" ? "Nueva campaña" : "Dashboard")]).map((label, index, trail) => <Fragment key={`${label}-${index}`}>{index > 0 && <Icon name="chevron" size={13} />}{index === trail.length - 1 ? <strong aria-current="page">{label}</strong> : <span>{label}</span>}</Fragment>)}</nav><div className="topbar-tools"><label className="search"><Icon name="search" size={18} /><span className="sr-only">Buscar, disponible próximamente</span><input type="search" placeholder="Buscar en tu espacio..." disabled /></label><PreviewButton className="icon-button notification" aria-label="Notificaciones, próximamente"><Icon name="bell" /><span /></PreviewButton><span className="topbar-divider" /><span className="avatar small" aria-label={userName}>{userInitials}</span></div></header><main id="main-content" tabIndex={-1}>{children}</main></div>
  </div>;
}
