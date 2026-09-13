"use client";

// MVP-13B, repaired MVP-13B-B (Notifications concurrency P1): real
// Notifications section. There is deliberately NO "Guardar cambios"
// button here — each toggle issues its own immediate, single-key PATCH
// (MVP-13B-B contract clarification #1), never all five booleans at
// once. Save model is "immediate per toggle" but NEVER concurrent: at
// most one notification PATCH may be in flight from this component at a
// time (`submittingKey`, not a Set) — ALL FIVE switches are disabled for
// the whole section while any one mutation is pending, not merely the
// switch being changed. This closes the single-client reachability of a
// real backend read-modify-write race (app/workspaces/repository.py's
// NotificationPreferenceRepository has no row lock/version check on the
// shared `toggles` JSONB column) — two notification PATCHes that were
// previously allowed to overlap could cause one's committed change to be
// silently reverted by the other's stale-based merge. Serializing every
// mutation through this single client removes that path entirely for
// ordinary single-user/single-tab interaction; it does NOT protect
// against two separate tabs/devices/API clients racing the same backend
// row — that remains an open backend-only gap (no row lock exists),
// deliberately out of scope for this frontend-only repair.
//
// Server's returned NotificationsPublic remains the sole source of
// truth: a successful PATCH replaces `confirmed` wholesale with the
// response, never a locally-computed merge; a failed PATCH leaves
// `confirmed` completely untouched (no optimistic value is ever
// retained).

import { useState } from "react";
import { notificationDefinitions } from "@/lib/settings-demo-data";
import { updateWorkspaceSettings } from "@/lib/api/settings";
import { describeSettingsError } from "@/lib/settings/error-messages";
import type { NotificationKey, NotificationsPublic, WorkspaceSettingsResponse } from "@/types/settings";

export function NotificationsSection({
  notifications,
  workspaceId,
  onUpdated,
}: {
  notifications: NotificationsPublic;
  workspaceId: string;
  onUpdated: (data: WorkspaceSettingsResponse) => void;
}) {
  const [confirmed, setConfirmed] = useState<NotificationsPublic>(notifications);
  const [submittingKey, setSubmittingKey] = useState<NotificationKey | null>(null);
  const [error, setError] = useState("");
  // "Adjusting state when a prop changes" (React's own recommended
  // alternative to a setState-in-effect sync, already used by
  // WorkspaceSection/AiPreferencesSection) — resets `confirmed` only
  // when the server value itself changes, never on every render. Note:
  // this repair required touching this sync mechanism only because the
  // lint gate now flags the effect-based form in this file's changed
  // structure — the sync's own behavior (never overwriting a pending
  // mutation or an error) is unchanged.
  const [syncedNotifications, setSyncedNotifications] = useState<NotificationsPublic>(notifications);
  if (notifications !== syncedNotifications) {
    setSyncedNotifications(notifications);
    setConfirmed(notifications);
  }

  async function toggle(key: NotificationKey) {
    // Defense-in-depth: the disabled switches already prevent this in
    // normal UI use, but a second call (e.g. a synthetic event) must
    // still never start a second, overlapping PATCH.
    if (submittingKey !== null) return;
    setSubmittingKey(key);
    setError("");
    const nextValue = !confirmed[key];
    try {
      const response = await updateWorkspaceSettings(workspaceId, { notifications: { [key]: nextValue } });
      onUpdated(response);
      setConfirmed(response.notifications);
    } catch (submitError) {
      setError(describeSettingsError(submitError));
    } finally {
      setSubmittingKey(null);
    }
  }

  return (
    <div className="settings-section">
      <div className="settings-section-heading">
        <h2>Notificaciones</h2>
        <p>Elige qué avisos quieres recibir. Cada cambio se guarda de inmediato.</p>
      </div>
      <ul className="panel settings-notifications-list">
        {notificationDefinitions.map((item) => (
          <li key={item.id}>
            <div>
              <span id={`notify-${item.id}-label`}>{item.label}</span>
              <p>{item.description}</p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={confirmed[item.id]}
              aria-labelledby={`notify-${item.id}-label`}
              aria-busy={submittingKey === item.id}
              disabled={submittingKey !== null}
              className={`settings-switch${confirmed[item.id] ? " is-on" : ""}`}
              onClick={() => toggle(item.id)}
            >
              <span className="settings-switch-thumb" aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="settings-feedback">
          {error}
        </p>
      )}
    </div>
  );
}
