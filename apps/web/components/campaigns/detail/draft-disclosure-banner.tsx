import { Icon } from "@/components/ui/icon";

/** Shared truthful framing for every deterministic-bootstrap output tab
 * (Research/Audience today; Strategy/Plan in a later phase) — one
 * reusable component so the wording never drifts between tabs. */
export function DraftDisclosureBanner() {
  return (
    <p className="muted small-text" role="note">
      <Icon name="spark" size={14} /> Este es un borrador inicial generado a partir de los datos de tu campaña. No
      incluye investigación externa ni validación independiente.
    </p>
  );
}
