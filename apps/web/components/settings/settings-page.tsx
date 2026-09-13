"use client";

// MVP-13B: real Settings page. Exactly one authoritative
// `GET /workspaces/{id}/settings` is issued on mount (covering the
// Workspace-name + AI-preferences + Notifications sections in one round
// trip) — it is never re-issued merely because the user switches tabs.
// Profile has its own independent source (the existing session via
// useAuth()) and issues no GET of its own at all.

import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useAuth } from "@/lib/auth/auth-context";
import { getWorkspaceSettings } from "@/lib/api/settings";
import { describeSettingsError } from "@/lib/settings/error-messages";
import { settingsTabs } from "@/lib/settings-demo-data";
import type { SettingsTab, WorkspaceSettingsResponse } from "@/types/settings";
import { ProfileSection } from "./profile-section";
import { WorkspaceSection } from "./workspace-section";
import { AiPreferencesSection } from "./ai-preferences-section";
import { IntegrationsSection } from "./integrations-section";
import { NotificationsSection } from "./notifications-section";
import { BillingSection } from "./billing-section";

type SettingsState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: WorkspaceSettingsResponse };

function SettingsLoadingPanel() {
  return (
    <section className="panel">
      <p className="muted small-text" role="status">
        Cargando configuración…
      </p>
    </section>
  );
}

function SettingsErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section className="panel workspace-empty">
      <h3>No pudimos cargar la configuración en este momento.</h3>
      <p>
        {message}{" "}
        <button type="button" className="auth-text-button" onClick={onRetry}>
          Reintentar
        </button>
      </p>
    </section>
  );
}

export function SettingsPage() {
  const auth = useAuth();
  const [active, setActive] = useState<SettingsTab>("profile");
  const [state, setState] = useState<SettingsState>({ status: "loading" });
  const buttons = useRef<Partial<Record<SettingsTab, HTMLButtonElement | null>>>({});

  const workspaceId = auth.status === "authenticated" ? auth.session.workspace.id : null;
  const role = auth.status === "authenticated" ? auth.session.membership.role : null;

  function load(id: string) {
    setState({ status: "loading" });
    getWorkspaceSettings(id)
      .then((data) => setState({ status: "ready", data }))
      .catch((error) => setState({ status: "error", message: describeSettingsError(error) }));
  }

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    getWorkspaceSettings(workspaceId)
      .then((data) => {
        if (!cancelled) setState({ status: "ready", data });
      })
      .catch((error) => {
        if (!cancelled) setState({ status: "error", message: describeSettingsError(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  function selectTab(tab: SettingsTab, focus = false) {
    setActive(tab);
    if (focus) buttons.current[tab]?.focus({ preventScroll: true });
  }

  function handleKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next =
      event.key === "ArrowDown"
        ? (index + 1) % settingsTabs.length
        : event.key === "ArrowUp"
          ? (index - 1 + settingsTabs.length) % settingsTabs.length
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? settingsTabs.length - 1
              : null;
    if (next !== null) {
      event.preventDefault();
      selectTab(settingsTabs[next].id, true);
    }
  }

  function handleUpdated(data: WorkspaceSettingsResponse) {
    setState({ status: "ready", data });
  }

  function renderWorkspaceScopedSection(
    render: (data: WorkspaceSettingsResponse, workspaceId: string, role: string) => ReactNode,
  ) {
    if (state.status === "loading") return <SettingsLoadingPanel />;
    if (state.status === "error") {
      return <SettingsErrorPanel message={state.message} onRetry={() => workspaceId && load(workspaceId)} />;
    }
    if (!workspaceId || !role) return <SettingsLoadingPanel />;
    return render(state.data, workspaceId, role);
  }

  return (
    <div className="dashboard settings-page">
      <header className="page-heading">
        <div>
          <div className="eyebrow">
            <span /> TU CONFIGURACIÓN
          </div>
          <h1>Configuración</h1>
          <p>Administra tu espacio de trabajo y las preferencias de Impulso.</p>
        </div>
      </header>
      <div className="settings-layout">
        <nav className="settings-nav" role="tablist" aria-orientation="vertical" aria-label="Secciones de configuración">
          {settingsTabs.map((tab, index) => (
            <button
              type="button"
              role="tab"
              key={tab.id}
              id={`settings-tab-${tab.id}`}
              aria-selected={active === tab.id}
              aria-controls={`settings-panel-${tab.id}`}
              tabIndex={active === tab.id ? 0 : -1}
              ref={(element) => {
                buttons.current[tab.id] = element;
              }}
              onClick={() => selectTab(tab.id)}
              onKeyDown={(event) => handleKey(event, index)}
            >
              {tab.label}
            </button>
          ))}
        </nav>
        {settingsTabs.map((tab) => (
          <section
            key={tab.id}
            role="tabpanel"
            id={`settings-panel-${tab.id}`}
            aria-labelledby={`settings-tab-${tab.id}`}
            tabIndex={0}
            hidden={active !== tab.id}
            className="settings-panel"
          >
            {tab.id === "profile" && <ProfileSection />}
            {tab.id === "workspace" &&
              renderWorkspaceScopedSection((data, id, r) => (
                <WorkspaceSection workspace={data.workspace} role={r} workspaceId={id} onUpdated={handleUpdated} />
              ))}
            {tab.id === "ai" &&
              renderWorkspaceScopedSection((data, id, r) => (
                <AiPreferencesSection aiPreferences={data.ai_preferences} role={r} workspaceId={id} onUpdated={handleUpdated} />
              ))}
            {tab.id === "notifications" &&
              renderWorkspaceScopedSection((data, id) => (
                <NotificationsSection notifications={data.notifications} workspaceId={id} onUpdated={handleUpdated} />
              ))}
            {tab.id === "integrations" && <IntegrationsSection />}
            {tab.id === "billing" && <BillingSection />}
          </section>
        ))}
      </div>
    </div>
  );
}
