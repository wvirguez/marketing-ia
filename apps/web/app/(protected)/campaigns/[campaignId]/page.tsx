import { AppShell } from "@/components/layout/app-shell";
import { CampaignDetail } from "@/components/campaigns/detail/campaign-detail";

export default async function CampaignDetailPage(props: PageProps<"/campaigns/[campaignId]">) {
  const { campaignId } = await props.params;
  return (
    <AppShell section="campaigns" breadcrumbs={["Campañas", "Detalle de campaña"]}>
      <CampaignDetail campaignId={campaignId} />
    </AppShell>
  );
}
