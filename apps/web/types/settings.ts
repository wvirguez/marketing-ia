import type { IconName } from "@/components/ui/icon";

// Mirrors apps/api/app/workspaces/schemas.py's Settings DTOs exactly
// (MVP-13B). No speculative field exists here: businessType/website/
// country/currency/workspace-level language/timezone, AI responseLanguage/
// requireApproval, and any billing/integration persistence field have no
// backend counterpart and are never modeled.

export interface WorkspaceProfilePublic {
  name: string;
}

export interface AIPreferencesPublic {
  tone: string | null;
  depth: string | null;
  creativity: string | null;
}

export interface NotificationsPublic {
  campaign_ready: boolean;
  content_review: boolean;
  metrics_available: boolean;
  analysis_complete: boolean;
  weekly_summary: boolean;
}

export type NotificationKey = keyof NotificationsPublic;

export interface WorkspaceSettingsResponse {
  workspace: WorkspaceProfilePublic;
  ai_preferences: AIPreferencesPublic;
  notifications: NotificationsPublic;
}

export interface WorkspaceProfilePatch {
  name: string;
}

export interface AIPreferencesPatch {
  tone?: string | null;
  depth?: string | null;
  creativity?: string | null;
}

export type NotificationsPatch = Partial<Record<NotificationKey, boolean>>;

export interface WorkspaceSettingsPatchRequest {
  workspace?: WorkspaceProfilePatch;
  ai_preferences?: AIPreferencesPatch;
  notifications?: NotificationsPatch;
}

// --- Frontend-only vocabulary for ai_preferences.tone/depth/creativity ---
// The backend validates these only against a bounded, machine-safe pattern
// (^[a-z][a-z0-9_]{0,31}$) — no canonical enum is frozen for any of the
// three (MVP-13B-A §L). These unions are this frontend's own chosen option
// set, not a backend-defined contract.
export type AiToneValue = "profesional" | "cercano" | "directo" | "educativo";
export type AiDepthValue = "breve" | "equilibrado" | "detallado";
export type AiCreativityValue = "conservadora" | "equilibrada" | "creativa";

// --- Static UI catalogs (not persisted account state) ---

export type IntegrationItem = {
  id: string;
  name: string;
  description: string;
  icon: IconName;
};

export type NotificationSetting = {
  id: NotificationKey;
  label: string;
  description: string;
};

export type PlanTier = {
  id: string;
  name: string;
  price: string;
  tagline: string;
  features: string[];
  recommended?: boolean;
};

export type SettingsTab = "profile" | "workspace" | "ai" | "integrations" | "notifications" | "billing";
