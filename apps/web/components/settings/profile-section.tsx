"use client";

// MVP-13B: real Profile section. Editable fields are exactly the ones the
// backend supports (apps/api/app/users/schemas.py::UserPatchRequest):
// display_name, preferences.locale, preferences.timezone. `email` has no
// PATCH field at all and is rendered read-only; `firstName`/`lastName`/
// "Cargo"/avatar have no backend equivalent and are not rendered.
//
// Source of truth on mount is the existing session (useAuth()) — no
// redundant GET /users/me is issued, since GET /auth/session already
// returns a fresh UserPublic including preferences.

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useAuth } from "@/lib/auth/auth-context";
import { updateCurrentUser } from "@/lib/api/auth";
import { describeSettingsError } from "@/lib/settings/error-messages";
import { languageOptions, timezoneOptions } from "@/lib/settings-demo-data";
import type { UserPatchRequest, UserPublic } from "@/types/auth";

const securityItems = [
  { label: "Contraseña", value: "Administrada próximamente por el sistema de autenticación" },
  { label: "Sesiones activas", value: "No disponible" },
  { label: "Autenticación en dos pasos", value: "Próximamente" },
];

type Draft = { displayName: string; locale: string; timezone: string };

function draftFromUser(user: UserPublic): Draft {
  return {
    displayName: user.display_name,
    locale: user.preferences.locale ?? "",
    timezone: user.preferences.timezone ?? "",
  };
}

export function ProfileSection() {
  const auth = useAuth();
  const session = auth.status === "authenticated" ? auth.session : null;

  const [confirmed, setConfirmed] = useState<UserPublic | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const initializedRef = useRef(false);

  // Initialize exactly once from the session — a later session object
  // (e.g. from auth.refresh() after our own successful PATCH) must never
  // clobber an in-progress edit or the just-confirmed PATCH result.
  useEffect(() => {
    if (session && !initializedRef.current) {
      initializedRef.current = true;
      setConfirmed(session.user);
      setDraft(draftFromUser(session.user));
    }
  }, [session]);

  function update<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((previous) => (previous ? { ...previous, [key]: value } : previous));
    setSaved(false);
    setError("");
  }

  const hasChanges =
    !!draft &&
    !!confirmed &&
    (draft.displayName !== confirmed.display_name ||
      (draft.locale || null) !== confirmed.preferences.locale ||
      (draft.timezone || null) !== confirmed.preferences.timezone);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!draft || !confirmed || submitting || !hasChanges) return;

    const payload: UserPatchRequest = {};
    if (draft.displayName !== confirmed.display_name) payload.display_name = draft.displayName;
    const localeChanged = (draft.locale || null) !== confirmed.preferences.locale;
    const timezoneChanged = (draft.timezone || null) !== confirmed.preferences.timezone;
    if (localeChanged || timezoneChanged) {
      payload.preferences = {};
      if (localeChanged) payload.preferences.locale = draft.locale || null;
      if (timezoneChanged) payload.preferences.timezone = draft.timezone || null;
    }

    setSubmitting(true);
    setError("");
    setSaved(false);
    try {
      const updated = await updateCurrentUser(payload);
      // The PATCH response is the persistence success boundary — it is
      // what the form displays as truth, independent of anything below.
      setConfirmed(updated);
      setDraft(draftFromUser(updated));
      setSaved(true);
      // Best-effort sync of the rest of the app (sidebar name, etc.). A
      // failure here must never be reported as a save failure, must never
      // revert the already-confirmed PATCH result, and must never trigger
      // a second PATCH.
      void auth.refresh().catch(() => {});
    } catch (submitError) {
      setError(describeSettingsError(submitError));
    } finally {
      setSubmitting(false);
    }
  }

  if (!session || !draft || !confirmed) {
    return (
      <section className="panel">
        <p className="muted small-text" role="status">
          Cargando perfil…
        </p>
      </section>
    );
  }

  return (
    <div className="settings-section">
      <div className="settings-section-heading">
        <h2>Perfil</h2>
      </div>
      <form className="panel settings-form" onSubmit={handleSubmit} noValidate>
        <div className="settings-fields">
          <div className="settings-field">
            <label htmlFor="profile-display-name">Nombre</label>
            <input
              id="profile-display-name"
              type="text"
              value={draft.displayName}
              onChange={(event) => update("displayName", event.target.value)}
              required
            />
          </div>
          <div className="settings-field">
            <label htmlFor="profile-email">Correo</label>
            <input id="profile-email" type="email" value={confirmed.email} disabled readOnly />
          </div>
          <div className="settings-field">
            <label htmlFor="profile-locale">Idioma</label>
            <select id="profile-locale" value={draft.locale} onChange={(event) => update("locale", event.target.value)}>
              <option value="">Sin preferencia</option>
              {languageOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="settings-field">
            <label htmlFor="profile-timezone">Zona horaria</label>
            <select id="profile-timezone" value={draft.timezone} onChange={(event) => update("timezone", event.target.value)}>
              <option value="">Sin preferencia</option>
              {timezoneOptions.map((option) => (
                <option key={option}>{option}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="settings-form-actions">
          <button className="button primary" type="submit" disabled={submitting || !hasChanges} aria-busy={submitting}>
            {submitting ? "Guardando…" : "Guardar cambios"}
          </button>
          <p role="status" aria-live="polite" className="settings-feedback">
            {submitting ? "Guardando…" : saved ? "Cambios guardados." : ""}
          </p>
        </div>
        {error && (
          <p role="alert" className="settings-feedback">
            {error}
          </p>
        )}
      </form>
      <section className="panel settings-security">
        <h3>Seguridad</h3>
        <ul className="settings-security-list">
          {securityItems.map((item) => (
            <li key={item.label}>
              <span>{item.label}</span>
              <span className="settings-security-value">{item.value}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
