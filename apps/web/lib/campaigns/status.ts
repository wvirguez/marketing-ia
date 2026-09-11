// Pure presentation mapping from the backend's real Campaign/CampaignRun
// status enums (apps/api/app/campaigns/models.py::CampaignStatus,
// CampaignRunStatus) to a Spanish business-facing label and a `.status`
// tone class. This never derives, infers, or transitions state — it only
// labels the exact string the backend already returned.

const CAMPAIGN_STATUS_LABELS: Record<string, string> = {
  DRAFT: "Borrador",
  SUBMITTED: "Enviada",
  ORCHESTRATING: "En orquestación",
  AWAITING_HUMAN_INPUT: "Esperando tu respuesta",
  READY_FOR_EXECUTION: "Lista para ejecutar",
  ACTIVE: "Activa",
  PAUSED: "Pausada",
  COMPLETED: "Completada",
  ARCHIVED: "Archivada",
  CANCELLED: "Cancelada",
};

const CAMPAIGN_STATUS_TONE: Record<string, string> = {
  DRAFT: "draft",
  SUBMITTED: "draft",
  ORCHESTRATING: "warning",
  AWAITING_HUMAN_INPUT: "warning",
  READY_FOR_EXECUTION: "warning",
  ACTIVE: "success",
  PAUSED: "warning",
  COMPLETED: "success",
  ARCHIVED: "draft",
  CANCELLED: "draft",
};

export function campaignStatusLabel(status: string): string {
  return CAMPAIGN_STATUS_LABELS[status] ?? status;
}

export function campaignStatusTone(status: string): string {
  return CAMPAIGN_STATUS_TONE[status] ?? "draft";
}

const CAMPAIGN_RUN_STATUS_LABELS: Record<string, string> = {
  CREATED: "Creada",
  RUNNING: "En ejecución",
  AWAITING_HUMAN_DECISION: "Esperando tu decisión",
  COMPLETED: "Completada",
  FAILED: "Fallida",
  CANCELLED: "Cancelada",
};

const CAMPAIGN_RUN_STATUS_TONE: Record<string, string> = {
  CREATED: "draft",
  RUNNING: "warning",
  AWAITING_HUMAN_DECISION: "warning",
  COMPLETED: "success",
  FAILED: "draft",
  CANCELLED: "draft",
};

export function campaignRunStatusLabel(status: string): string {
  return CAMPAIGN_RUN_STATUS_LABELS[status] ?? status;
}

export function campaignRunStatusTone(status: string): string {
  return CAMPAIGN_RUN_STATUS_TONE[status] ?? "draft";
}

export function formatCampaignDate(value: string): string {
  return new Intl.DateTimeFormat("es", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(value));
}
