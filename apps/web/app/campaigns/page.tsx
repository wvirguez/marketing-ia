import { AppShell } from "@/components/layout/app-shell";
import { CampaignsList } from "@/components/campaigns/campaigns-list";

export default function CampaignsPage() {
  return <AppShell section="campaigns" breadcrumbs={["Campañas"]}><CampaignsList /></AppShell>;
}
