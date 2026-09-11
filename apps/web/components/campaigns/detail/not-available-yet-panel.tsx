import { Icon, type IconName } from "@/components/ui/icon";

/** Truthful placeholder for a downstream domain tab that MVP-03 does not
 * yet wire (Research/Audience/Strategy/Plan/Content/Assets/Tracking/
 * Metrics) — never fixture/demo content presented as if it belonged to
 * this real campaign. */
export function NotAvailableYetPanel({ icon, title, body }: { icon: IconName; title: string; body: string }) {
  return (
    <section className="panel workspace-empty">
      <span className="workspace-empty-symbol"><Icon name={icon} size={32} /></span>
      <h2>{title}</h2>
      <p>{body}</p>
    </section>
  );
}
