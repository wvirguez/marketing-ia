"use client";

import { useState, type FormEvent } from "react";
import { countryOptions, currencyOptions, initialWorkspace, languageOptions, timezoneOptions, workspaceDemoId } from "@/lib/settings-demo-data";
import type { WorkspaceDraft } from "@/types/settings";

export function WorkspaceSection() {
  const [draft, setDraft] = useState<WorkspaceDraft>(initialWorkspace);
  const [feedback, setFeedback] = useState("");

  function update<K extends keyof WorkspaceDraft>(key: K, value: WorkspaceDraft[K]) {
    setDraft(previous => ({ ...previous, [key]: value }));
    setFeedback("");
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setFeedback("Cambios guardados en esta sesión.");
  }

  return (
    <div className="settings-section">
      <div className="settings-section-heading"><h2>Espacio de trabajo</h2><p>Configura la información general de tu espacio de trabajo de demostración.</p></div>
      <form className="panel settings-form" onSubmit={handleSubmit} noValidate>
        <div className="settings-fields">
          <div className="settings-field"><label htmlFor="workspace-name">Nombre del espacio</label><input id="workspace-name" type="text" value={draft.name} onChange={event => update("name", event.target.value)} /></div>
          <div className="settings-field"><label htmlFor="workspace-type">Tipo de negocio</label><input id="workspace-type" type="text" value={draft.businessType} onChange={event => update("businessType", event.target.value)} /></div>
          <div className="settings-field"><label htmlFor="workspace-website">Sitio web (opcional)</label><input id="workspace-website" type="url" placeholder="https://" value={draft.website} onChange={event => update("website", event.target.value)} /></div>
          <div className="settings-field"><label htmlFor="workspace-country">País</label><select id="workspace-country" value={draft.country} onChange={event => update("country", event.target.value)}>{countryOptions.map(option => <option key={option}>{option}</option>)}</select></div>
          <div className="settings-field"><label htmlFor="workspace-currency">Moneda</label><select id="workspace-currency" value={draft.currency} onChange={event => update("currency", event.target.value)}>{currencyOptions.map(option => <option key={option}>{option}</option>)}</select></div>
          <div className="settings-field"><label htmlFor="workspace-language">Idioma predeterminado</label><select id="workspace-language" value={draft.language} onChange={event => update("language", event.target.value)}>{languageOptions.map(option => <option key={option}>{option}</option>)}</select></div>
          <div className="settings-field"><label htmlFor="workspace-timezone">Zona horaria predeterminada</label><select id="workspace-timezone" value={draft.timezone} onChange={event => update("timezone", event.target.value)}>{timezoneOptions.map(option => <option key={option}>{option}</option>)}</select></div>
        </div>
        <div className="settings-field settings-workspace-id"><span>Workspace ID</span><code>{workspaceDemoId}</code><span className="demo-pill">Demo</span></div>
        <p className="settings-local-note">Los cambios no se guardarán al recargar.</p>
        <div className="settings-form-actions">
          <button className="button primary" type="submit">Guardar cambios</button>
          <p role="status" aria-live="polite" className="settings-feedback">{feedback}</p>
        </div>
      </form>
      <section className="panel settings-danger-zone">
        <h3>Zona de riesgo</h3>
        <p>Estas acciones requieren autenticación, confirmación y backend seguro.</p>
        <div className="settings-danger-actions">
          <button type="button" className="button danger" disabled aria-describedby="settings-danger-note">Eliminar workspace</button>
          <button type="button" className="button danger" disabled aria-describedby="settings-danger-note">Eliminar cuenta</button>
        </div>
        <p id="settings-danger-note" className="settings-local-note">No disponible en esta demostración.</p>
      </section>
    </div>
  );
}
