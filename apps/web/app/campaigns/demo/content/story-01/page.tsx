import { AppShell } from "@/components/layout/app-shell";
import { ContentDetail } from "@/components/content/content-detail";
import { campaignDemo } from "@/lib/campaign-demo-data";
import { storyDetail } from "@/lib/content-demo-data";

export default function StoryContentPage() {
  return (
    <AppShell section="campaigns" breadcrumbs={["Campañas", campaignDemo.name, "Contenido", storyDetail.title]}>
      <ContentDetail detail={storyDetail} />
    </AppShell>
  );
}
