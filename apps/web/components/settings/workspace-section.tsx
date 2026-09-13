"use client";

// MVP-13B: real Workspace section. The only backend-writable workspace
// field is `name` (apps/api/app/workspaces/models.py::Workspace has only
// `name`/`slug`/`organization_id`) — businessType/website/country/
// currency/workspace-level language/timezone, the workspace-ID badge, and
// "delete workspace"/"delete account" have no backend equivalent and are
// not rendered.
//
// Editing `name` is Admin-gated on the backend (OWNER/ADMIN only); role is
// sourced from the existing session (session.membership.role), never a
// separate GET.

import { useState, type FormEvent } from "react";
import { updateWorkspaceSettings } from "@/lib/api/settings";
import { describeSettingsError } from "@/lib/settings/error-messages";
import type { WorkspaceProfilePublic, WorkspaceSettingsResponse } from "@/types/settings";

export function WorkspaceSection({
  workspace,
  role,
  workspaceId,
  onUpdated,
}: {
  workspace: WorkspaceProfilePublic;
  role: string;
  workspaceId: string;
  onUpdated: (data: WorkspaceSettingsResponse) => void;
}) {
  const canEdit = role === "OWNER" || role === "ADMIN";
  const [draft, setDraft] = useState(workspace.name);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  // "Adjusting state when a prop changes" (React's own recommended
  // alternative to a setState-in-effect sync) — resets the draft only
  // when the confirmed server value itself changes (e.g. after our own
  // successful save), never on every render.
  const [syncedName, setSyncedName] = useState(workspace.name);
  if (workspace.name !== syncedName) {
    setSyncedName(workspace.name);
    setDraft(workspace.name);
  }

  const hasChanges = draft !== workspace.name;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!canEdit || submitting || !hasChanges) return;
    setSubmitting(true);
    setError("");
    setSaved(false);
    try {
      const response = await updateWorkspaceSettings(workspaceId, { workspace: { name: draft } });
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
        <h2>Espacio de trabajo</h2>
      </div>
      <form className="panel settings-form" onSubmit={handleSubmit} noValidate>
        <div className="settings-fields">
          <div className="settings-field">
            <label htmlFor="workspace-name">Nombre del espacio</label>
            <input
              id="workspace-name"
              type="text"
              value={draft}
              disabled={!canEdit}
              onChange={(event) => {
                setDraft(event.target.value);
                setSaved(false);
                setError("");
              }}
              required
            />
          </div>
        </div>
        {!canEdit && (
          <p className="settings-local-note">
            Solo las personas propietarias o administradoras de este espacio de trabajo pueden cambiar este valor.
          </p>
        )}
        {canEdit && (
          <div className="settings-form-actions">
            <button className="button primary" type="submit" disabled={submitting || !hasChanges} aria-busy={submitting}>
              {submitting ? "Guardando…" : "Guardar cambios"}
            </button>
            <p role="status" aria-live="polite" className="settings-feedback">
              {submitting ? "Guardando…" : saved ? "Cambios guardados." : ""}
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
