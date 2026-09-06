import { AppShell } from "@/components/layout/app-shell";
import { ContentDetail } from "@/components/content/content-detail";
import { campaignDemo } from "@/lib/campaign-demo-data";
import { carouselDetail } from "@/lib/content-demo-data";

export default function CarouselContentPage() {
  return (
    <AppShell section="campaigns" breadcrumbs={["Campañas", campaignDemo.name, "Contenido", carouselDetail.title]}>
      <ContentDetail detail={carouselDetail} />
    </AppShell>
  );
}
