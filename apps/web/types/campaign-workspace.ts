export type WorkspaceTab = "overview" | "research" | "audience" | "strategy" | "plan" | "content" | "creatives" | "paid" | "tracking" | "metrics";
export type DeliverableStatus = "Completado" | "En producción" | "Pendiente";
export type ContentItem = {
  id: string;
  title: string;
  format: "Reel" | "Carrusel" | "Story";
  objective: string;
  status: "Planificado" | "En producción" | "Pendiente";
  cta: string;
  week: 1 | 2;
};
export type CampaignWorkspace = {
  name: string;
  description: string;
  product: string;
  price: string;
  channel: string;
  status: string;
  progress: number;
  highlights: { label: string; value: string }[];
  workflow: { label: string; state: "done" | "current" | "pending" }[];
  deliverables: { title: string; status: DeliverableStatus; tab: WorkspaceTab }[];
};
