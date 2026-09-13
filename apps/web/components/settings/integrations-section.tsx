// MVP-13B: no Integrations bounded context exists on the backend at all
// (verified in the MVP-13B-A audit) — this section is read-only roadmap
// content. No "Conectar" button, no fake connected/disconnected status,
// and no network call of any kind.

import { integrations } from "@/lib/settings-demo-data";
import { Icon } from "@/components/ui/icon";

export function IntegrationsSection() {
  return (
    <div className="settings-section">
      <div className="settings-section-heading">
        <h2>Integraciones</h2>
        <p>Estas integraciones estarán disponibles próximamente. Por ahora no hay ninguna conexión real ni se solicitan credenciales.</p>
      </div>
      <ul className="settings-integrations-grid">
        {integrations.map((integration) => (
          <li className="panel settings-integration-card" key={integration.id}>
            <div className="settings-integration-top">
              <span className="icon-tile blue">
                <Icon name={integration.icon} size={20} />
              </span>
            </div>
            <h3>{integration.name}</h3>
            <p>{integration.description}</p>
            <p className="settings-local-note">Próximamente</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
