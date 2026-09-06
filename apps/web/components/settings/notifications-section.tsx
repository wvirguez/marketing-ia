"use client";

import { useState } from "react";
import { initialNotifications } from "@/lib/settings-demo-data";

export function NotificationsSection() {
  const [settings, setSettings] = useState<Record<string, boolean>>(() => Object.fromEntries(initialNotifications.map(item => [item.id, true])));

  function toggle(id: string) {
    setSettings(previous => ({ ...previous, [id]: !previous[id] }));
  }

  return (
    <div className="settings-section">
      <div className="settings-section-heading"><h2>Notificaciones</h2><p>Elige qué avisos quieres recibir. Esta preferencia es local a esta sesión.</p></div>
      <ul className="panel settings-notifications-list">
        {initialNotifications.map(item => (
          <li key={item.id}>
            <div>
              <span id={`notify-${item.id}-label`}>{item.label}</span>
              <p>{item.description}</p>
            </div>
            <button type="button" role="switch" aria-checked={settings[item.id]} aria-labelledby={`notify-${item.id}-label`} className={`settings-switch${settings[item.id] ? " is-on" : ""}`} onClick={() => toggle(item.id)}>
              <span className="settings-switch-thumb" aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
      <p className="settings-local-note">Los cambios no se guardarán al recargar.</p>
    </div>
  );
}
