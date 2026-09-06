import type { IconName } from "@/components/ui/icon";

export type ProfileDraft = {
  firstName: string;
  lastName: string;
  email: string;
  role: string;
  language: string;
  timezone: string;
};

export type WorkspaceDraft = {
  name: string;
  businessType: string;
  website: string;
  country: string;
  currency: string;
  language: string;
  timezone: string;
};

export type AiTone = "Profesional" | "Cercano" | "Directo" | "Educativo";
export type AiDepth = "Breve" | "Equilibrado" | "Detallado";
export type AiCreativity = "Conservadora" | "Equilibrada" | "Creativa";

export type AiPreferencesDraft = {
  responseLanguage: string;
  tone: AiTone;
  depth: AiDepth;
  creativity: AiCreativity;
  requireApproval: boolean;
};

export type IntegrationItem = {
  id: string;
  name: string;
  description: string;
  icon: IconName;
};

export type NotificationSetting = {
  id: string;
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
