import { AppShell } from "@/components/layout/app-shell";
import { CampaignWorkspace } from "@/components/campaigns/workspace/campaign-workspace";

export default function CampaignDemoPage() {
  return <AppShell section="campaigns" context="Método Canino en Casa"><CampaignWorkspace /></AppShell>;
}
