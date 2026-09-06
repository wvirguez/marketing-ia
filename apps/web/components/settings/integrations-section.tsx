"use client";

import { useState } from "react";
import { integrations } from "@/lib/settings-demo-data";
import { Icon } from "@/components/ui/icon";

export function IntegrationsSection() {
  const [feedback, setFeedback] = useState<{ id: string; message: string } | null>(null);

  return (
    <div className="settings-section">
      <div className="settings-section-heading"><h2>Integraciones</h2><p>Estas integraciones estarán disponibles cuando conectemos el backend. Por ahora no se solicitan credenciales ni se guardan tokens.</p></div>
      <ul className="settings-integrations-grid">
        {integrations.map(integration => (
          <li className="panel settings-integration-card" key={integration.id}>
            <div className="settings-integration-top"><span className="icon-tile blue"><Icon name={integration.icon} size={20} /></span><span className="settings-integration-status">No conectado</span></div>
            <h3>{integration.name}</h3>
            <p>{integration.description}</p>
            <button type="button" className="button secondary" onClick={() => setFeedback({ id: integration.id, message: "Las integraciones estarán disponibles cuando conectemos el backend." })}>Conectar</button>
            <p role="status" aria-live="polite" className="settings-feedback">{feedback?.id === integration.id ? feedback.message : ""}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
