export const campaignStatuses = ["Borrador", "En preparación", "Activa", "Pausada", "Completada"] as const;
export const campaignTypes = ["Ebook", "Curso online", "Servicio", "Consultoría"] as const;
export const campaignChannels = ["Instagram", "Facebook", "Meta Ads", "Email", "Multicanal"] as const;
export type CampaignListItem = {
  id: string;
  name: string;
  productType: typeof campaignTypes[number];
  status: typeof campaignStatuses[number];
  channel: typeof campaignChannels[number];
  updatedDate: string;
  progress: number;
  description: string;
  workspaceHref?: "/campaigns/demo";
};
// Fixed presentation fixtures: statuses and progress do not represent execution.
export const campaignList: readonly CampaignListItem[] = [
  { id: "canino", name: "Método Canino en Casa", productType: "Curso online", status: "En preparación", channel: "Instagram", updatedDate: "2026-09-05", progress: 65, description: "Un método práctico para mejorar la obediencia canina desde casa.", workspaceHref: "/campaigns/demo" },
  { id: "ebook", name: "Ebook Finanzas para Emprendedores", productType: "Ebook", status: "Activa", channel: "Email", updatedDate: "2026-09-04", progress: 85, description: "Conceptos financieros para organizar y hacer crecer un emprendimiento." },
  { id: "consultoria", name: "Consultoría de Marketing", productType: "Servicio", status: "Borrador", channel: "Multicanal", updatedDate: "2026-09-03", progress: 20, description: "Una propuesta de acompañamiento para negocios que buscan claridad." },
  { id: "fitness", name: "Programa Fitness en Casa", productType: "Curso online", status: "Pausada", channel: "Meta Ads", updatedDate: "2026-09-02", progress: 50, description: "Rutinas de entrenamiento que se adaptan al día a día." },
  { id: "productividad", name: "Guía de Productividad", productType: "Ebook", status: "Completada", channel: "Facebook", updatedDate: "2026-09-01", progress: 100, description: "Herramientas para organizar prioridades y aprovechar el tiempo." },
  { id: "agencia", name: "Agencia para PYMEs", productType: "Servicio", status: "Activa", channel: "Multicanal", updatedDate: "2026-08-31", progress: 75, description: "Servicios de marketing para pequeñas y medianas empresas." },
  { id: "reposteria", name: "Curso de Repostería", productType: "Curso online", status: "En preparación", channel: "Instagram", updatedDate: "2026-08-30", progress: 40, description: "Recetas y técnicas para empezar a crear postres desde casa." },
  { id: "mentoria", name: "Mentoría de Marca Personal", productType: "Consultoría", status: "Borrador", channel: "Email", updatedDate: "2026-08-29", progress: 10, description: "Un recorrido para definir una identidad profesional con propósito." },
];
export const campaignStatusTone: Record<CampaignListItem["status"], string> = {
  Borrador: "draft", "En preparación": "preparing", Activa: "active", Pausada: "paused", Completada: "complete",
};
const normalize = (value: string) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es").trim();
export function filterCampaigns(query: string, status: string, productType: string, channel: string) {
  const search = normalize(query);
  return campaignList.filter(campaign =>
    (!status || campaign.status === status) && (!productType || campaign.productType === productType) && (!channel || campaign.channel === channel) &&
    normalize(`${campaign.name} ${campaign.productType} ${campaign.channel}`).includes(search));
}
export function campaignDate(value: string) {
  return new Intl.DateTimeFormat("es", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));
}
