"use client";

import { useState } from "react";
import { Icon } from "@/components/ui/icon";
import { PreviewButton } from "@/components/ui/preview-button";
import { WorkspaceStatus } from "@/components/campaigns/workspace/workspace-status";
import type { ContentDetailData } from "@/types/content-detail";

type CopyKey = "script" | "caption" | "cta";
const copyLabels: Record<CopyKey, string> = { script: "Copiar guion", caption: "Copiar caption", cta: "Copiar CTA" };
const clipboardFallback = "No se pudo copiar automáticamente. Selecciona y copia el texto manualmente.";

export function ContentActions({ detail }: { detail: ContentDetailData }) {
  const [copyState, setCopyState] = useState<{ key: CopyKey; ok: boolean } | null>(null);
  const [approvalMessage, setApprovalMessage] = useState("");

  async function handleCopy(key: CopyKey) {
    const text = detail.copy[key];
    try {
      if (!navigator.clipboard) throw new Error("clipboard-unavailable");
      await navigator.clipboard.writeText(text);
      setCopyState({ key, ok: true });
    } catch {
      setCopyState({ key, ok: false });
    }
  }

  function handleApproval() {
    setApprovalMessage("Esta acción estará disponible cuando conectemos el backend.");
  }

  const liveMessage = copyState ? (copyState.ok ? "Copiado" : clipboardFallback) : "";

  return (
    <div className="content-actions">
      <section className="panel content-actions-panel">
        <h2>Acciones sobre el contenido</h2>
        <div className="content-copy-row">
          {(Object.keys(copyLabels) as CopyKey[]).map(key => (
            <button key={key} type="button" className="button content-copy-button" onClick={() => handleCopy(key)}>
              <Icon name="content" size={15} />
              {copyLabels[key]}
              {copyState?.key === key && copyState.ok && <span className="copy-feedback">Copiado</span>}
            </button>
          ))}
        </div>
        {copyState && !copyState.ok && <p className="content-copy-error" role="alert">{clipboardFallback}</p>}
        <span aria-live="polite" className="sr-only">{liveMessage}</span>
        <div className="content-download-row">
          <PreviewButton className="button" aria-describedby="content-download-soon"><Icon name="arrow" size={15} />Descargar</PreviewButton>
          <span id="content-download-soon" className="content-soon-note">Próximamente</span>
        </div>
      </section>
      <section className="panel content-approval-panel">
        <div className="content-approval-heading"><h2>Estado del contenido</h2><WorkspaceStatus value={detail.status} /></div>
        <p className="content-approval-note">Esta acción es solo una vista de aprobación local. No representa la aprobación de Governance ni cambia el estado real de la campaña.</p>
        <div className="content-approval-actions">
          <button type="button" className="button primary" onClick={handleApproval}><Icon name="check" size={15} />Aprobar</button>
          <button type="button" className="button secondary" onClick={handleApproval}>Solicitar cambios</button>
        </div>
        <p aria-live="polite" className="content-approval-message">{approvalMessage}</p>
      </section>
    </div>
  );
}
