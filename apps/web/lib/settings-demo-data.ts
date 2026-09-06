import type { AiPreferencesDraft, IntegrationItem, NotificationSetting, PlanTier, ProfileDraft, SettingsTab, WorkspaceDraft } from "@/types/settings";

export const settingsTabs: { id: SettingsTab; label: string }[] = [
  { id: "profile", label: "Perfil" },
  { id: "workspace", label: "Espacio de trabajo" },
  { id: "ai", label: "Preferencias de IA" },
  { id: "integrations", label: "Integraciones" },
  { id: "notifications", label: "Notificaciones" },
  { id: "billing", label: "Plan y facturación" },
];

export const languageOptions = ["Español", "English"];
export const timezoneOptions = ["America/Toronto", "America/Mexico_City", "America/Bogota", "America/Santiago", "Europe/Madrid"];
export const countryOptions = ["Canada", "México", "Colombia", "Chile", "España", "Estados Unidos"];
export const currencyOptions = ["USD", "CAD", "MXN", "COP", "EUR"];

export const initialProfile: ProfileDraft = {
  firstName: "Emilia",
  lastName: "Martínez",
  email: "emilia@ejemplo.com",
  role: "Administradora",
  language: "Español",
  timezone: "America/Toronto",
};

export const initialWorkspace: WorkspaceDraft = {
  name: "Emilia Studio",
  businessType: "Agencia de Marketing Digital",
  website: "",
  country: "Canada",
  currency: "USD",
  language: "Español",
  timezone: "America/Toronto",
};

export const workspaceDemoId = "DEMO-WORKSPACE-001";

export const aiToneOptions: AiPreferencesDraft["tone"][] = ["Profesional", "Cercano", "Directo", "Educativo"];
export const aiDepthOptions: AiPreferencesDraft["depth"][] = ["Breve", "Equilibrado", "Detallado"];
export const aiCreativityOptions: AiPreferencesDraft["creativity"][] = ["Conservadora", "Equilibrada", "Creativa"];

export const initialAiPreferences: AiPreferencesDraft = {
  responseLanguage: "Español",
  tone: "Profesional",
  depth: "Equilibrado",
  creativity: "Equilibrada",
  requireApproval: true,
};

export const integrations: IntegrationItem[] = [
  { id: "meta", name: "Meta", description: "Publicación y anuncios en Facebook e Instagram.", icon: "image" },
  { id: "google-ads", name: "Google Ads", description: "Campañas de búsqueda y display.", icon: "target" },
  { id: "google-analytics", name: "Google Analytics", description: "Medición de tráfico y conversiones.", icon: "chart" },
  { id: "instagram", name: "Instagram", description: "Publicación orgánica y métricas.", icon: "image" },
  { id: "tiktok", name: "TikTok", description: "Publicación y anuncios en TikTok.", icon: "image" },
  { id: "youtube", name: "YouTube", description: "Publicación de video y métricas de canal.", icon: "image" },
  { id: "email", name: "Email Marketing", description: "Envío de campañas y automatizaciones por correo.", icon: "content" },
  { id: "ai-provider", name: "OpenRouter / AI Provider", description: "Motor de generación de IA para contenido y estrategia.", icon: "spark" },
];

export const initialNotifications: NotificationSetting[] = [
  { id: "campaign-ready", label: "Campaña lista", description: "Cuando una campaña completa su preparación inicial." },
  { id: "content-review", label: "Contenido listo para revisión", description: "Cuando una pieza de contenido está lista para tu aprobación." },
  { id: "metrics-available", label: "Métricas disponibles", description: "Cuando hay nuevas métricas para revisar." },
  { id: "analysis-complete", label: "Análisis completado", description: "Cuando un análisis de rendimiento termina." },
  { id: "weekly-summary", label: "Resumen semanal", description: "Un resumen semanal de la actividad de tu espacio." },
];

export const currentPlan = { name: "Studio Demo", status: "Demostración" };

export const planTiers: PlanTier[] = [
  { id: "starter", name: "Starter", price: "Precio por definir", tagline: "Para empezar a organizar tus campañas.", features: ["1 espacio de trabajo", "Campañas ilimitadas de ejemplo", "Soporte por correo"] },
  { id: "professional", name: "Professional", price: "Precio por definir", tagline: "Para equipos que necesitan más control.", features: ["Todo lo de Starter", "Preferencias de IA avanzadas", "Integraciones (próximamente)"], recommended: true },
  { id: "agency", name: "Agency", price: "Precio por definir", tagline: "Para agencias con múltiples clientes.", features: ["Todo lo de Professional", "Múltiples espacios de trabajo", "Soporte prioritario"] },
];
