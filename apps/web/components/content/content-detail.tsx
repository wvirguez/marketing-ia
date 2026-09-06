import type { ContentDetailData } from "@/types/content-detail";
import { ContentHeader } from "./content-header";
import { ContentScript } from "./content-script";
import { ContentPreview } from "./content-preview";
import { ContentActions } from "./content-actions";

export function ContentDetail({ detail }: { detail: ContentDetailData }) {
  return (
    <div className="dashboard content-detail-page">
      <ContentHeader detail={detail} />
      <div className="content-grid">
        <ContentScript detail={detail} />
        <div className="content-preview-column">
          <ContentPreview detail={detail} />
          <ContentActions detail={detail} />
        </div>
      </div>
      <footer className="dashboard-footer"><span>Vista de detalle de contenido · Datos de demostración.</span><span>Impulso <span aria-hidden="true">✦</span> Tu creatividad, más lejos.</span></footer>
    </div>
  );
}
