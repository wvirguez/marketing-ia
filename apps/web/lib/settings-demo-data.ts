import type {
  AiCreativityValue,
  AiDepthValue,
  AiToneValue,
  IntegrationItem,
  NotificationSetting,
  PlanTier,
  SettingsTab,
} from "@/types/settings";

export const settingsTabs: { id: SettingsTab; label: string }[] = [
  { id: "profile", label: "Perfil" },
  { id: "workspace", label: "Espacio de trabajo" },
  { id: "ai", label: "Preferencias de IA" },
  { id: "integrations", label: "Integraciones" },
  { id: "notifications", label: "Notificaciones" },
  { id: "billing", label: "Plan y facturación" },
];

// `preferences.locale`/`preferences.timezone` are opaque, unenforced
// strings on the backend (no canonical vocabulary is frozen for either,
// apps/api/app/users/models.py) — this option catalog is a frontend-only
// convention, not a backend-defined enum.
export const languageOptions: { value: string; label: string }[] = [
  { value: "es", label: "Español" },
  { value: "en", label: "English" },
];

export const timezoneOptions = [
  "America/Toronto",
  "America/Mexico_City",
  "America/Bogota",
  "America/Santiago",
  "Europe/Madrid",
];

// Same "frontend-only convention" note as above — the backend validates
// tone/depth/creativity only against a bounded machine-safe pattern
// (^[a-z][a-z0-9_]{0,31}$), never against a fixed enum (MVP-13B-A §L).
export const aiToneOptions: { value: AiToneValue; label: string }[] = [
  { value: "profesional", label: "Profesional" },
  { value: "cercano", label: "Cercano" },
  { value: "directo", label: "Directo" },
  { value: "educativo", label: "Educativo" },
];

export const aiDepthOptions: { value: AiDepthValue; label: string }[] = [
  { value: "breve", label: "Breve" },
  { value: "equilibrado", label: "Equilibrado" },
  { value: "detallado", label: "Detallado" },
];

export const aiCreativityOptions: { value: AiCreativityValue; label: string }[] = [
  { value: "conservadora", label: "Conservadora" },
  { value: "equilibrada", label: "Equilibrada" },
  { value: "creativa", label: "Creativa" },
];

// Static label/description catalog only — the actual boolean values
// always come from the server (WorkspaceSettingsResponse.notifications),
// never seeded here.
export const notificationDefinitions: NotificationSetting[] = [
  { id: "campaign_ready", label: "Campaña lista", description: "Cuando una campaña completa su preparación inicial." },
  { id: "content_review", label: "Contenido listo para revisión", description: "Cuando una pieza de contenido está lista para tu aprobación." },
  { id: "metrics_available", label: "Métricas disponibles", description: "Cuando hay nuevas métricas para revisar." },
  { id: "analysis_complete", label: "Análisis completado", description: "Cuando un análisis de rendimiento termina." },
  { id: "weekly_summary", label: "Resumen semanal", description: "Un resumen semanal de la actividad de tu espacio." },
];

// No backend integrations bounded context exists — this catalog is
// descriptive roadmap content only (MVP-13B-A §N), never a real
// connection state.
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

// No backend billing bounded context exists — this is a roadmap preview
// only (MVP-13B-A §O), never a real active-plan/subscription record.
export const planTiers: PlanTier[] = [
  { id: "starter", name: "Starter", price: "Precio por definir", tagline: "Para empezar a organizar tus campañas.", features: ["1 espacio de trabajo", "Campañas ilimitadas de ejemplo", "Soporte por correo"] },
  { id: "professional", name: "Professional", price: "Precio por definir", tagline: "Para equipos que necesitan más control.", features: ["Todo lo de Starter", "Preferencias de IA avanzadas", "Integraciones (próximamente)"], recommended: true },
  { id: "agency", name: "Agency", price: "Precio por definir", tagline: "Para agencias con múltiples clientes.", features: ["Todo lo de Professional", "Múltiples espacios de trabajo", "Soporte prioritario"] },
];
