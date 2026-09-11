import { AppShell } from "@/components/layout/app-shell";
import { NewCampaignForm } from "@/components/campaigns/new-campaign-form";

export default function NewCampaignPage() {
  return (
    <AppShell section="campaigns">
      <div className="dashboard new-campaign-page">
        <div className="page-heading"><div><div className="eyebrow"><span /> DE LA IDEA A LA ACCIÓN</div><h1>Crea una nueva campaña</h1><p>Cuéntanos qué quieres lanzar. Impulso organizará el resto contigo.</p></div><span className="new-campaign-badge">Un nuevo comienzo <span aria-hidden="true">✦</span></span></div>
        <NewCampaignForm />
      </div>
    </AppShell>
  );
}
