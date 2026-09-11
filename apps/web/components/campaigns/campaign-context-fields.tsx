export interface CampaignContextValues {
  productType: string;
  price: string;
  audience: string;
  budget: string;
  channel: string;
}

export const EMPTY_CAMPAIGN_CONTEXT: CampaignContextValues = {
  productType: "",
  price: "",
  audience: "",
  budget: "",
  channel: "",
};

export function CampaignContextFields({
  values,
  onChange,
}: {
  values: CampaignContextValues;
  onChange: <K extends keyof CampaignContextValues>(field: K, value: CampaignContextValues[K]) => void;
}) {
  return (
    <details className="campaign-context">
      <summary>Agregar más contexto <span>Opcional</span></summary>
      <p>Comparte lo que ya sabes. Puedes empezar sólo con una frase.</p>
      <div className="context-grid">
        <label>Tipo de producto<select value={values.productType} onChange={(event) => onChange("productType", event.target.value)}><option value="">Selecciona un tipo</option>{["Ebook", "Curso online", "Servicio", "Consultoría", "Membresía", "Producto físico", "Otro"].map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Precio<input type="text" inputMode="decimal" placeholder="Ej. 29 USD" maxLength={60} value={values.price} onChange={(event) => onChange("price", event.target.value)} /></label>
        <label>Audiencia inicial<input type="text" placeholder="¿A quién quieres llegar?" maxLength={200} value={values.audience} onChange={(event) => onChange("audience", event.target.value)} /></label>
        <label>Presupuesto<input type="text" inputMode="decimal" placeholder="Ej. 500 USD al mes" maxLength={60} value={values.budget} onChange={(event) => onChange("budget", event.target.value)} /></label>
        <label>Canal principal<select value={values.channel} onChange={(event) => onChange("channel", event.target.value)}><option value="">Selecciona un canal</option>{["Instagram", "Facebook", "TikTok", "YouTube", "Email", "Multicanal", "Aún no lo sé"].map(value => <option key={value}>{value}</option>)}</select></label>
      </div>
    </details>
  );
}
