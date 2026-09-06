export function CampaignContextFields() {
  return (
    <details className="campaign-context">
      <summary>Agregar más contexto <span>Opcional</span></summary>
      <p>Comparte lo que ya sabes. Puedes empezar sólo con una frase.</p>
      <div className="context-grid">
        <label>Tipo de producto<select defaultValue=""><option value="">Selecciona un tipo</option>{["Ebook", "Curso online", "Servicio", "Consultoría", "Membresía", "Producto físico", "Otro"].map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Precio<input type="text" inputMode="decimal" placeholder="Ej. 29 USD" maxLength={60} /></label>
        <label>Audiencia inicial<input type="text" placeholder="¿A quién quieres llegar?" maxLength={200} /></label>
        <label>Presupuesto<input type="text" inputMode="decimal" placeholder="Ej. 500 USD al mes" maxLength={60} /></label>
        <label>Canal principal<select defaultValue=""><option value="">Selecciona un canal</option>{["Instagram", "Facebook", "TikTok", "YouTube", "Email", "Multicanal", "Aún no lo sé"].map(value => <option key={value}>{value}</option>)}</select></label>
      </div>
    </details>
  );
}
