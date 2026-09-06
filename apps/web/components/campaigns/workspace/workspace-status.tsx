export function WorkspaceStatus({ value }: { value: string }) {
  const tone = ["Completado", "Configurado", "Lista"].includes(value) ? "success" : ["En producción", "En preparación"].includes(value) ? "warning" : "draft";
  return <span className={`status ${tone}`}><span aria-hidden="true" />{value}</span>;
}
