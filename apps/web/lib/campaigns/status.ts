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

// MVP-05F: pure presentation mapping from the backend's real
// ContentPieceStatus enum (apps/api/app/content/models.py) to a Spanish
// business-facing label and a `.status` tone class. Every one of the 9
// backend values keeps its own distinct label — never collapsed into
// "Aprobado"/"Listo para publicar"/"Final"/"Validado" for DRAFT, since
// PRODUCED != APPROVED and READY FOR REVIEW != APPROVED FOR DISTRIBUTION
// (MVP-05E's own governance invariants). This only labels the exact
// string the backend already returned — it never derives, infers, or
// transitions state, and it never implies an approval/production/
// distribution action exists for the user to take.
const CONTENT_PIECE_STATUS_LABELS: Record<string, string> = {
  DRAFT: "Borrador",
  IN_PRODUCTION: "En producción",
  PRODUCED: "Producido",
  READY_FOR_REVIEW: "Listo para revisión",
  REVISION_REQUESTED: "Cambios solicitados",
  APPROVED: "Aprobado",
  READY_FOR_DISTRIBUTION: "Listo para distribución",
  DISTRIBUTED: "Distribuido",
  ARCHIVED: "Archivado",
};

const CONTENT_PIECE_STATUS_TONE: Record<string, string> = {
  DRAFT: "draft",
  IN_PRODUCTION: "warning",
  PRODUCED: "warning",
  READY_FOR_REVIEW: "warning",
  REVISION_REQUESTED: "warning",
  APPROVED: "success",
  READY_FOR_DISTRIBUTION: "success",
  DISTRIBUTED: "success",
  ARCHIVED: "draft",
};

export function contentPieceStatusLabel(status: string): string {
  return CONTENT_PIECE_STATUS_LABELS[status] ?? status;
}

export function contentPieceStatusTone(status: string): string {
  return CONTENT_PIECE_STATUS_TONE[status] ?? "draft";
}
