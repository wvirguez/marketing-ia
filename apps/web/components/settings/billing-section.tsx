import { currentPlan, planTiers } from "@/lib/settings-demo-data";
import { PreviewButton } from "@/components/ui/preview-button";
import { Icon } from "@/components/ui/icon";

export function BillingSection() {
  return (
    <div className="settings-section">
      <div className="settings-section-heading"><h2>Plan y facturación</h2><p>Vista previa del futuro modelo de planes de Impulso. No hay pagos ni facturación real conectados.</p></div>
      <section className="panel settings-current-plan">
        <div><span className="demo-pill">{currentPlan.status}</span><h3>{currentPlan.name}</h3><p>Acceso completo a las funciones de demostración de Impulso.</p></div>
      </section>
      <div className="settings-plan-grid">
        {planTiers.map(tier => (
          <article className={`panel settings-plan-card${tier.recommended ? " is-recommended" : ""}`} key={tier.id}>
            {tier.recommended && <span className="settings-plan-badge">Recomendado</span>}
            <h3>{tier.name}</h3>
            <p className="settings-plan-price">{tier.price}</p>
            <p className="settings-plan-tagline">{tier.tagline}</p>
            <ul className="settings-plan-features">{tier.features.map(feature => <li key={feature}><Icon name="check" size={14} />{feature}</li>)}</ul>
            <PreviewButton className="button primary" aria-describedby={`plan-${tier.id}-soon`}>Próximamente</PreviewButton>
            <span id={`plan-${tier.id}-soon`} className="settings-local-note">Disponible próximamente</span>
          </article>
        ))}
      </div>
    </div>
  );
}
