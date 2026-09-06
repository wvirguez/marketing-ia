import Link from "next/link";
import { Icon } from "@/components/ui/icon";
import { WorkspaceStatus } from "@/components/campaigns/workspace/workspace-status";
import type { ContentDetailData } from "@/types/content-detail";

export function ContentHeader({ detail }: { detail: ContentDetailData }) {
  return (
    <header className="content-detail-header">
      <Link href="/campaigns/demo" className="content-detail-back">
        <Icon name="arrow" size={14} style={{ transform: "rotate(180deg)" }} />
        Volver a la campaña
      </Link>
      <div className="content-detail-title-row">
        <span className="content-detail-icon"><Icon name={detail.format === "Reel" ? "image" : "content"} size={26} /></span>
        <div>
          <div className="eyebrow"><span /> CONTENIDO DE CAMPAÑA</div>
          <h1>{detail.title}</h1>
        </div>
        <WorkspaceStatus value={detail.status} />
      </div>
      <dl className="content-detail-meta">
        <div><dt>Formato</dt><dd>{detail.format}</dd></div>
        <div><dt>Objetivo</dt><dd>{detail.objective}</dd></div>
        <div><dt>Etapa del funnel</dt><dd>{detail.funnelStage}</dd></div>
        <div><dt>CTA</dt><dd>{detail.cta}</dd></div>
        <div><dt>Canal</dt><dd>{detail.channel}</dd></div>
      </dl>
      <div className="workspace-demo-notice">
        <span className="demo-pill">CONTENIDO DE DEMOSTRACIÓN</span>
        <p>Este contenido es un ejemplo fijo para revisar la experiencia de detalle. No fue publicado ni generado por un agente real.</p>
      </div>
    </header>
  );
}
