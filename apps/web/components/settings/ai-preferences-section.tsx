"use client";

import { useState, type FormEvent } from "react";
import { aiCreativityOptions, aiDepthOptions, aiToneOptions, initialAiPreferences, languageOptions } from "@/lib/settings-demo-data";
import type { AiPreferencesDraft } from "@/types/settings";

function ChoiceGroup<T extends string>({ legend, name, options, value, onChange }: { legend: string; name: string; options: readonly T[]; value: T; onChange: (value: T) => void }) {
  return (
    <fieldset className="settings-choice-group">
      <legend>{legend}</legend>
      <div className="settings-choice-options">
        {options.map(option => (
          <label key={option} className={`settings-choice${option === value ? " is-selected" : ""}`}>
            <input type="radio" name={name} value={option} checked={option === value} onChange={() => onChange(option)} />
            {option}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function AiPreferencesSection() {
  const [draft, setDraft] = useState<AiPreferencesDraft>(initialAiPreferences);
  const [feedback, setFeedback] = useState("");

  function update<K extends keyof AiPreferencesDraft>(key: K, value: AiPreferencesDraft[K]) {
    setDraft(previous => ({ ...previous, [key]: value }));
    setFeedback("");
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setFeedback("Preferencias guardadas en esta sesión.");
  }

  return (
    <div className="settings-section">
      <div className="settings-section-heading"><h2>Preferencias de IA</h2><p>Estas preferencias ayudarán a personalizar la experiencia cuando conectemos el motor de IA.</p></div>
      <form className="panel settings-form" onSubmit={handleSubmit} noValidate>
        <div className="settings-field">
          <label htmlFor="ai-response-language">Idioma de respuesta predeterminado</label>
          <select id="ai-response-language" value={draft.responseLanguage} onChange={event => update("responseLanguage", event.target.value)}>{languageOptions.map(option => <option key={option}>{option}</option>)}</select>
        </div>
        <ChoiceGroup legend="Tono preferido" name="ai-tone" options={aiToneOptions} value={draft.tone} onChange={value => update("tone", value)} />
        <ChoiceGroup legend="Profundidad del contenido" name="ai-depth" options={aiDepthOptions} value={draft.depth} onChange={value => update("depth", value)} />
        <ChoiceGroup legend="Creatividad" name="ai-creativity" options={aiCreativityOptions} value={draft.creativity} onChange={value => update("creativity", value)} />
        <div className="settings-switch-row">
          <div>
            <span id="ai-approval-label">Solicitar revisión antes de avanzar en decisiones importantes</span>
            <p>Cuando está activo, Impulso pedirá tu aprobación antes de avanzar en pasos clave.</p>
          </div>
          <button type="button" role="switch" aria-checked={draft.requireApproval} aria-labelledby="ai-approval-label" className={`settings-switch${draft.requireApproval ? " is-on" : ""}`} onClick={() => update("requireApproval", !draft.requireApproval)}>
            <span className="settings-switch-thumb" aria-hidden="true" />
          </button>
        </div>
        <p className="settings-local-note">Los cambios no se guardarán al recargar.</p>
        <div className="settings-form-actions">
          <button className="button primary" type="submit">Guardar preferencias</button>
          <p role="status" aria-live="polite" className="settings-feedback">{feedback}</p>
        </div>
      </form>
    </div>
  );
}
