import { campaignList } from "@/lib/campaign-list-data";
import type { Campaign } from "@/types/dashboard";
import type { IconName } from "@/components/ui/icon";

// Presentation fixtures only. No live metrics or agent execution.
export const stats: { label: string; value: string; change: string; icon: IconName; tone: string; bars: number[] }[] = [
  { label: "Campañas totales", value: String(campaignList.length), change: "+12%", icon: "campaign", tone: "blue", bars: [25, 40, 32, 60, 48, 76, 90] },
  { label: "Campañas activas", value: String(campaignList.filter(campaign => campaign.status === "Activa").length), change: "+8%", icon: "target", tone: "mint", bars: [20, 35, 30, 52, 65, 58, 85] },
  { label: "Contenidos creados", value: "24", change: "+24%", icon: "content", tone: "violet", bars: [25, 20, 40, 50, 45, 75, 95] },
  { label: "Conversiones", value: "1,284", change: "+18%", icon: "chart", tone: "orange", bars: [20, 38, 30, 55, 70, 60, 90] },
];
export const campaigns: Campaign[] = [
  { id: "canino", name: "Método Canino en Casa", category: "Curso online", status: "En preparación", progress: 65, date: "2026-09-05", dateLabel: "05 sep, 2026", initials: "MC", tone: "blue" },
  { id: "ebook", name: "Ebook Finanzas para Emprendedores", category: "Lead Generation", status: "Activa", progress: 85, date: "2026-09-04", dateLabel: "04 sep, 2026", initials: "EF", tone: "mint" },
  { id: "consultoria", name: "Consultoría de Marketing", category: "Servicios", status: "Borrador", progress: 20, date: "2026-09-03", dateLabel: "03 sep, 2026", initials: "CM", tone: "violet" },
];
