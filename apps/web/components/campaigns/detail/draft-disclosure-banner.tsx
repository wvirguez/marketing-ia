import { Icon } from "@/components/ui/icon";

/** Shared truthful framing for every deterministic-bootstrap output tab
 * (Research, Audience, Strategy, Plan) — one reusable component so the
 * wording never drifts between tabs. Stays accurate for Strategy/Plan
 * too: neither is externally validated, and Plan is derived only from
 * the persisted Strategy/CampaignBrief, never from approved production
 * authorization. */
export function DraftDisclosureBanner() {
  return (
    <p className="muted small-text" role="note">
      <Icon name="spark" size={14} /> Este es un borrador inicial generado a partir de los datos de tu campaña. No
      incluye investigación externa ni validación independiente.
    </p>
  );
}
