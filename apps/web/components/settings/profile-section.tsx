"use client";

import { useState, type FormEvent } from "react";
import { initialProfile, languageOptions, timezoneOptions } from "@/lib/settings-demo-data";
import type { ProfileDraft } from "@/types/settings";

const securityItems = [
  { label: "Contraseña", value: "Administrada próximamente por el sistema de autenticación" },
  { label: "Sesiones activas", value: "No disponible" },
  { label: "Autenticación en dos pasos", value: "Próximamente" },
];

export function ProfileSection() {
  const [draft, setDraft] = useState<ProfileDraft>(initialProfile);
  const [feedback, setFeedback] = useState("");
  const initials = `${draft.firstName.charAt(0)}${draft.lastName.charAt(0)}`.toUpperCase() || "EM";

  function update<K extends keyof ProfileDraft>(key: K, value: ProfileDraft[K]) {
    setDraft(previous => ({ ...previous, [key]: value }));
    setFeedback("");
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setFeedback("Cambios guardados en esta sesión.");
  }

  return (
    <div className="settings-section">
      <div className="settings-section-heading"><h2>Perfil</h2><p>Esta información es de demostración y sólo se usa para previsualizar la experiencia.</p></div>
      <form className="panel settings-form" onSubmit={handleSubmit} noValidate>
        <div className="settings-avatar-row">
          <span className="settings-avatar" aria-hidden="true">{initials}</span>
          <p className="settings-avatar-note">El avatar usa tus iniciales. Esta demostración no admite imágenes de perfil.</p>
        </div>
        <div className="settings-fields">
          <div className="settings-field"><label htmlFor="profile-first-name">Nombre</label><input id="profile-first-name" type="text" value={draft.firstName} onChange={event => update("firstName", event.target.value)} /></div>
          <div className="settings-field"><label htmlFor="profile-last-name">Apellido</label><input id="profile-last-name" type="text" value={draft.lastName} onChange={event => update("lastName", event.target.value)} /></div>
          <div className="settings-field"><label htmlFor="profile-email">Correo</label><input id="profile-email" type="email" value={draft.email} onChange={event => update("email", event.target.value)} /></div>
          <div className="settings-field"><label htmlFor="profile-role">Cargo</label><input id="profile-role" type="text" value={draft.role} onChange={event => update("role", event.target.value)} /></div>
          <div className="settings-field"><label htmlFor="profile-language">Idioma</label><select id="profile-language" value={draft.language} onChange={event => update("language", event.target.value)}>{languageOptions.map(option => <option key={option}>{option}</option>)}</select></div>
          <div className="settings-field"><label htmlFor="profile-timezone">Zona horaria</label><select id="profile-timezone" value={draft.timezone} onChange={event => update("timezone", event.target.value)}>{timezoneOptions.map(option => <option key={option}>{option}</option>)}</select></div>
        </div>
        <p className="settings-local-note">Los cambios no se guardarán al recargar.</p>
        <div className="settings-form-actions">
          <button className="button primary" type="submit">Guardar cambios</button>
          <p role="status" aria-live="polite" className="settings-feedback">{feedback}</p>
        </div>
      </form>
      <section className="panel settings-security">
        <h3>Seguridad</h3>
        <ul className="settings-security-list">
          {securityItems.map(item => <li key={item.label}><span>{item.label}</span><span className="settings-security-value">{item.value}</span></li>)}
        </ul>
      </section>
    </div>
  );
}
