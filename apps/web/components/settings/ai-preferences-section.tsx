"use client";

// MVP-13B: real AI Preferences section. Only `tone`/`depth`/`creativity`
// exist on the backend (apps/api/app/workspaces/schemas.py::
// AIPreferencesPatch) — `responseLanguage`/`requireApproval` have no
// backend field and are not rendered. Editing is Admin-gated, same as
// Workspace. Each field is tracked independently so a save only ever
// submits the field(s) actually changed (MVP-13B-A §14) — never all
// three merely because one changed, and never a PATCH at all when
// nothing changed. `null` (no override) renders honestly as "no option
// selected" — no clear/reset UI is added for this MVP.

import { useState, type FormEvent } from "react";
import { aiCreativityOptions, aiDepthOptions, aiToneOptions } from "@/lib/settings-demo-data";
import { updateWorkspaceSettings } from "@/lib/api/settings";
import { describeSettingsError } from "@/lib/settings/error-messages";
import type { AIPreferencesPatch, AIPreferencesPublic, WorkspaceSettingsResponse } from "@/types/settings";

function ChoiceGroup({
  legend,
  name,
  options,
  value,
  disabled,
  onChange,
}: {
  legend: string;
  name: string;
  options: readonly { value: string; label: string }[];
  value: string | null;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <fieldset className="settings-choice-group" disabled={disabled}>
      <legend>{legend}</legend>
      <div className="settings-choice-options">
        {options.map((option) => (
          <label key={option.value} className={`settings-choice${option.value === value ? " is-selected" : ""}`}>
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={option.value === value}
              disabled={disabled}
              onChange={() => onChange(option.value)}
            />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

type AiField = "tone" | "depth" | "creativity";

export function AiPreferencesSection({
  aiPreferences,
  role,
  workspaceId,
  onUpdated,
}: {
  aiPreferences: AIPreferencesPublic;
  role: string;
  workspaceId: string;
  onUpdated: (data: WorkspaceSettingsResponse) => void;
}) {
  const canEdit = role === "OWNER" || role === "ADMIN";
  const [confirmed, setConfirmed] = useState<AIPreferencesPublic>(aiPreferences);
  const [draft, setDraft] = useState<AIPreferencesPublic>(aiPreferences);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  // Same "adjusting state when a prop changes" pattern as
  // WorkspaceSection — resets confirmed+draft only when the server value
  // itself changes, never on every render.
  const [syncedAiPreferences, setSyncedAiPreferences] = useState<AIPreferencesPublic>(aiPreferences);
  if (aiPreferences !== syncedAiPreferences) {
    setSyncedAiPreferences(aiPreferences);
    setConfirmed(aiPreferences);
    setDraft(aiPreferences);
  }

  function update(field: AiField, value: string) {
    setDraft((previous) => ({ ...previous, [field]: value }));
    setSaved(false);
    setError("");
  }

  const dirty: AIPreferencesPatch = {};
  (["tone", "depth", "creativity"] as AiField[]).forEach((field) => {
    if (draft[field] !== confirmed[field]) dirty[field] = draft[field];
  });
  const hasChanges = Object.keys(dirty).length > 0;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!canEdit || submitting || !hasChanges) return;
    setSubmitting(true);
    setError("");
    setSaved(false);
    try {
      const response = await updateWorkspaceSettings(workspaceId, { ai_preferences: dirty });
      onUpdated(response);
      setSaved(true);
    } catch (submitError) {
      setError(describeSettingsError(submitError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="settings-section">
      <div className="settings-section-heading">
        <h2>Preferencias de IA</h2>
        <p>Estas preferencias ayudarán a personalizar la experiencia cuando conectemos el motor de IA.</p>
      </div>
      <form className="panel settings-form" onSubmit={handleSubmit} noValidate>
        <ChoiceGroup legend="Tono preferido" name="ai-tone" options={aiToneOptions} value={draft.tone} disabled={!canEdit} onChange={(value) => update("tone", value)} />
        <ChoiceGroup legend="Profundidad del contenido" name="ai-depth" options={aiDepthOptions} value={draft.depth} disabled={!canEdit} onChange={(value) => update("depth", value)} />
        <ChoiceGroup legend="Creatividad" name="ai-creativity" options={aiCreativityOptions} value={draft.creativity} disabled={!canEdit} onChange={(value) => update("creativity", value)} />
        {!canEdit && (
          <p className="settings-local-note">
            Solo las personas propietarias o administradoras de este espacio de trabajo pueden cambiar estas preferencias.
          </p>
        )}
        {canEdit && (
          <div className="settings-form-actions">
            <button className="button primary" type="submit" disabled={submitting || !hasChanges} aria-busy={submitting}>
              {submitting ? "Guardando…" : "Guardar preferencias"}
            </button>
            <p role="status" aria-live="polite" className="settings-feedback">
              {submitting ? "Guardando…" : saved ? "Preferencias guardadas." : ""}
            </p>
          </div>
        )}
        {error && (
          <p role="alert" className="settings-feedback">
            {error}
          </p>
        )}
      </form>
    </div>
  );
}
