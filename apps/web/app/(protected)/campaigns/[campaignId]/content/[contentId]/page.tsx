import { AppShell } from "@/components/layout/app-shell";
import { ContentDetailView } from "@/components/campaigns/detail/content-detail-view";

export default async function ContentDetailPage(
  props: PageProps<"/campaigns/[campaignId]/content/[contentId]">,
) {
  const { campaignId, contentId } = await props.params;
  return (
    <AppShell section="campaigns" breadcrumbs={["Campañas", "Detalle de campaña", "Contenido"]}>
      <ContentDetailView key={`${campaignId}:${contentId}`} campaignId={campaignId} contentId={contentId} />
    </AppShell>
  );
}
