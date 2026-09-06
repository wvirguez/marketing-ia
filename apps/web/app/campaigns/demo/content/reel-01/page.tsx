import { AppShell } from "@/components/layout/app-shell";
import { ContentDetail } from "@/components/content/content-detail";
import { campaignDemo } from "@/lib/campaign-demo-data";
import { reelDetail } from "@/lib/content-demo-data";

export default function ReelContentPage() {
  return (
    <AppShell section="campaigns" breadcrumbs={["Campañas", campaignDemo.name, "Contenido", reelDetail.title]}>
      <ContentDetail detail={reelDetail} />
    </AppShell>
  );
}
