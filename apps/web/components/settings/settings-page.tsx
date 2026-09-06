"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import { settingsTabs } from "@/lib/settings-demo-data";
import type { SettingsTab } from "@/types/settings";
import { ProfileSection } from "./profile-section";
import { WorkspaceSection } from "./workspace-section";
import { AiPreferencesSection } from "./ai-preferences-section";
import { IntegrationsSection } from "./integrations-section";
import { NotificationsSection } from "./notifications-section";
import { BillingSection } from "./billing-section";

export function SettingsPage() {
  const [active, setActive] = useState<SettingsTab>("profile");
  const buttons = useRef<Partial<Record<SettingsTab, HTMLButtonElement | null>>>({});

  function selectTab(tab: SettingsTab, focus = false) {
    setActive(tab);
    if (focus) buttons.current[tab]?.focus({ preventScroll: true });
  }

  function handleKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = event.key === "ArrowDown" ? (index + 1) % settingsTabs.length : event.key === "ArrowUp" ? (index - 1 + settingsTabs.length) % settingsTabs.length : event.key === "Home" ? 0 : event.key === "End" ? settingsTabs.length - 1 : null;
    if (next !== null) { event.preventDefault(); selectTab(settingsTabs[next].id, true); }
  }

  return (
    <div className="dashboard settings-page">
      <header className="page-heading">
        <div>
          <div className="eyebrow"><span /> TU CONFIGURACIÓN</div>
          <h1>Configuración</h1>
          <p>Administra tu espacio de trabajo y las preferencias de Impulso.</p>
        </div>
      </header>
      <div className="workspace-demo-notice"><span className="demo-pill">Configuración de demostración</span><p>Los cambios se guardan solo en esta sesión del navegador. Nada se envía ni se conecta a un backend real.</p></div>
      <div className="settings-layout">
        <nav className="settings-nav" role="tablist" aria-orientation="vertical" aria-label="Secciones de configuración">
          {settingsTabs.map((tab, index) => (
            <button type="button" role="tab" key={tab.id} id={`settings-tab-${tab.id}`} aria-selected={active === tab.id} aria-controls={`settings-panel-${tab.id}`} tabIndex={active === tab.id ? 0 : -1} ref={element => { buttons.current[tab.id] = element; }} onClick={() => selectTab(tab.id)} onKeyDown={event => handleKey(event, index)}>{tab.label}</button>
          ))}
        </nav>
        {settingsTabs.map(tab => (
          <section key={tab.id} role="tabpanel" id={`settings-panel-${tab.id}`} aria-labelledby={`settings-tab-${tab.id}`} tabIndex={0} hidden={active !== tab.id} className="settings-panel">
            {tab.id === "profile" && <ProfileSection />}
            {tab.id === "workspace" && <WorkspaceSection />}
            {tab.id === "ai" && <AiPreferencesSection />}
            {tab.id === "integrations" && <IntegrationsSection />}
            {tab.id === "notifications" && <NotificationsSection />}
            {tab.id === "billing" && <BillingSection />}
          </section>
        ))}
      </div>
    </div>
  );
}
