export const metricFields = [
  { key: "impressions", label: "Impresiones" },
  { key: "reach", label: "Alcance" },
  { key: "clicks", label: "Clics" },
  { key: "leads", label: "Leads" },
  { key: "sales", label: "Ventas" },
  { key: "revenue", label: "Ingresos", money: true },
  { key: "spend", label: "Gasto publicitario", money: true },
  { key: "views", label: "Reproducciones de video", optional: true },
  { key: "saves", label: "Guardados", optional: true },
  { key: "shares", label: "Compartidos", optional: true },
  { key: "comments", label: "Comentarios", optional: true },
] as const;
export type MetricKey = typeof metricFields[number]["key"];
export type MetricsDraft = { start: string; end: string; channel: string } & Record<MetricKey, string>;
export const channels = ["Instagram", "Facebook", "Meta Ads", "TikTok", "YouTube", "Email", "Sitio web", "Otro"];
export const emptyMetrics: MetricsDraft = {
  start: "", end: "", channel: "Instagram", impressions: "", reach: "", clicks: "",
  leads: "", sales: "", revenue: "", spend: "", views: "", saves: "", shares: "", comments: "",
};
export type MetricsErrors = Partial<Record<keyof MetricsDraft | "performance", string>>;
export function validateMetrics(draft: MetricsDraft): MetricsErrors {
  const errors: MetricsErrors = {};
  for (const key of ["start", "end"] as const) {
    const value = draft[key];
    const parsed = new Date(`${value}T00:00:00Z`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value)
      errors[key] = "Ingresa una fecha válida.";
  }
  if (!errors.start && !errors.end && draft.end < draft.start) errors.end = "La fecha de fin debe ser igual o posterior a la de inicio.";
  if (!channels.includes(draft.channel)) errors.channel = "Selecciona un canal válido.";
  if (!metricFields.some(({ key }) => draft[key].trim() !== "")) errors.performance = "Ingresa al menos una métrica de rendimiento; cero también es válido.";
  for (const field of metricFields) {
    if (draft[field.key].trim() === "") continue;
    const value = Number(draft[field.key]);
    const money = "money" in field;
    if (!Number.isFinite(value) || value < 0 || value > Number.MAX_SAFE_INTEGER || (!money && !Number.isSafeInteger(value)) || (money && Math.abs(value * 100 - Math.round(value * 100)) > 0.0001))
      errors[field.key] = money ? "Ingresa un importe no negativo con hasta 2 decimales, dentro del rango seguro." : "Ingresa un número entero no negativo dentro del rango seguro.";
  }
  return errors;
}
export function metricValue(draft: MetricsDraft, key: MetricKey): number | null {
  return draft[key].trim() === "" ? null : Number(draft[key]);
}
function ratio(numerator: number | null, denominator: number | null, factor = 1): number | null {
  if (numerator === null || denominator === null || denominator <= 0) return null;
  const result = numerator / denominator * factor;
  return Number.isFinite(result) ? result : null;
}
export function derivedMetrics(draft: MetricsDraft) {
  const get = (key: MetricKey) => metricValue(draft, key);
  return [
    { label: "CTR", value: ratio(get("clicks"), get("impressions"), 100), format: "percent", formula: "Clics / impresiones × 100" },
    { label: "CPC", value: ratio(get("spend"), get("clicks")), format: "currency", formula: "Gasto / clics" },
    { label: "CPA", value: ratio(get("spend"), get("sales")), format: "currency", formula: "Gasto / ventas" },
    { label: "Tasa de conversión", value: ratio(get("sales"), get("clicks"), 100), format: "percent", formula: "Ventas / clics × 100" },
    { label: "ROAS", value: ratio(get("revenue"), get("spend")), format: "ratio", formula: "Ingresos / gasto" },
  ] as const;
}
export function formatMetric(value: number | null, format: "number" | "percent" | "currency" | "ratio" = "number") {
  if (value === null || !Number.isFinite(value)) return "—";
  if (format === "currency") return new Intl.NumberFormat("es", { style: "currency", currency: "USD", currencyDisplay: "code" }).format(value).replaceAll("\u00a0", " ");
  return new Intl.NumberFormat("es", { maximumFractionDigits: 2 }).format(value) + (format === "percent" ? " %" : format === "ratio" ? "×" : "");
}
export function dataQuality(draft: MetricsDraft | null) {
  if (!draft) return "Insuficiente";
  const has = (key: MetricKey) => metricValue(draft, key) !== null;
  if (draft.start && draft.end && has("impressions") && has("clicks") && ["leads", "sales", "revenue"].some(key => has(key as MetricKey))) return "Completo para revisión";
  return metricFields.filter(({ key }) => has(key)).length >= 2 ? "Parcial" : "Insuficiente";
}
