"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { channels, dataQuality, derivedMetrics, emptyMetrics, formatMetric, metricFields, metricValue, validateMetrics, type MetricsDraft, type MetricsErrors } from "@/lib/campaign-metrics";
import { Icon } from "@/components/ui/icon";

function MetricsForm({ initial, onSave, onCancel }: { initial: MetricsDraft; onSave: (draft: MetricsDraft) => void; onCancel: () => void }) {
  const [draft, setDraft] = useState({ ...initial });
  const [errors, setErrors] = useState<MetricsErrors>({});
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => { form.current?.querySelector<HTMLInputElement>("input")?.focus(); }, []);
  function submit(event: FormEvent) {
    event.preventDefault();
    const next = validateMetrics(draft);
    setErrors(next);
    if (Object.keys(next).length) {
      const key = Object.keys(next)[0];
      form.current?.querySelector<HTMLElement>(`#metrics-${key === "performance" ? "impressions" : key}`)?.focus();
    } else onSave(draft);
  }
  function field(key: keyof MetricsDraft, label: string, type: string, money = false) {
    const error = errors[key];
    return <div className="metrics-field" key={key}>
      <label htmlFor={`metrics-${key}`}>{label}{money ? " (USD)" : ""}</label>
      <input id={`metrics-${key}`} type={type} value={draft[key]} required={type === "date"} min={type === "number" ? 0 : undefined} max={type === "number" ? Number.MAX_SAFE_INTEGER : undefined} step={type === "number" ? money ? "0.01" : "1" : undefined} inputMode={type === "number" ? money ? "decimal" : "numeric" : undefined} aria-invalid={!!error} aria-describedby={[error ? `metrics-error-${key}` : "", type === "number" ? "metrics-performance-help metrics-performance-error" : ""].filter(Boolean).join(" ") || undefined} onChange={event => { setDraft({ ...draft, [key]: event.target.value }); setErrors(previous => ({ ...previous, [key]: undefined, performance: undefined })); }} />
      {error && <span className="metrics-error" id={`metrics-error-${key}`}>{error}</span>}
    </div>;
  }
  return <form className="panel workspace-document metrics-form" ref={form} noValidate onSubmit={submit} aria-labelledby="metrics-form-title">
    <h2 id="metrics-form-title">{initial.start ? "Editar métricas" : "Agregar métricas"}</h2>
    <p>Registra un periodo y un canal. Guardar reemplaza el registro temporal actual.</p>
    <fieldset><legend>Periodo (obligatorio)</legend><div className="metrics-fields">{field("start", "Fecha de inicio", "date")}{field("end", "Fecha de fin", "date")}
      <div className="metrics-field"><label htmlFor="metrics-channel">Canal</label><select id="metrics-channel" value={draft.channel} onChange={event => setDraft({ ...draft, channel: event.target.value })}>{channels.map(channel => <option key={channel}>{channel}</option>)}</select></div>
    </div></fieldset>
    <fieldset><legend>Rendimiento</legend><p id="metrics-performance-help">Agrega al menos una métrica. Deja en blanco los datos desconocidos; 0 indica un valor registrado. Importes en USD.</p><p id="metrics-performance-error" className="metrics-error">{errors.performance}</p><div className="metrics-fields">{metricFields.filter(field => !("optional" in field)).map(item => field(item.key, item.label, "number", "money" in item))}</div></fieldset>
    <fieldset><legend>Interacciones adicionales (opcionales)</legend><div className="metrics-fields">{metricFields.filter(field => "optional" in field).map(item => field(item.key, item.label, "number"))}</div></fieldset>
    <p role="alert">{Object.values(errors).some(Boolean) ? "Revisa los campos indicados antes de guardar." : ""}</p>
    <div className="metrics-actions"><button className="button primary" type="submit">Guardar métricas</button><button className="button secondary" type="button" onClick={onCancel}>Cancelar</button></div>
  </form>;
}

export function CampaignMetrics() {
  const [saved, setSaved] = useState<MetricsDraft | null>(null);
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [feedback, setFeedback] = useState("");
  const action = useRef<HTMLButtonElement>(null);
  const cancelDelete = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
  useEffect(() => {
    if (confirming) cancelDelete.current?.focus();
    else if (!editing && restoreFocus.current) { action.current?.focus(); restoreFocus.current = false; }
  }, [editing, confirming]);
  function finishEditing() { restoreFocus.current = true; setEditing(false); }
  const derived = saved ? derivedMetrics(saved) : [];
  return <div className="metrics-workspace">
    <div className="workspace-demo-notice"><span className="demo-pill">Datos temporales de demostración</span><p>Los datos se perderán al recargar o salir de esta página. No hay fuentes de datos conectadas.</p></div>
    <p className="metrics-feedback" role="status" aria-live="polite">{feedback}</p>
    {editing ? <MetricsForm initial={saved ?? emptyMetrics} onSave={draft => { setSaved(draft); setFeedback("Métricas guardadas temporalmente."); finishEditing(); }} onCancel={() => { setFeedback("Edición cancelada. No se modificaron los datos."); finishEditing(); }} /> : !saved ?
      <section className="panel workspace-empty"><span className="workspace-empty-symbol"><Icon name="chart" size={35} /></span><h2>Aún no hay métricas</h2><p>Cuando implementes tu campaña, agrega los resultados para que Impulso pueda ayudarte a analizar qué está funcionando y qué conviene revisar.</p><button ref={action} className="button primary" type="button" onClick={() => { setFeedback(""); setEditing(true); }}>Agregar métricas</button><span>También podrás conectar fuentes de datos más adelante.</span></section> : <>
      <section className="panel workspace-document">
        <div className="metrics-heading"><div><h2>Resumen de rendimiento</h2><p>Datos ingresados manualmente</p><p>{saved.start} → {saved.end} · {saved.channel} · USD</p></div><div className="metrics-actions"><button ref={action} className="button secondary" type="button" disabled={confirming} onClick={() => { setFeedback(""); setEditing(true); }}>Editar métricas</button><button className="button secondary" type="button" disabled={confirming} onClick={() => setConfirming(true)}>Eliminar datos de demostración</button></div></div>
        {confirming && <section className="metrics-confirm" aria-labelledby="metrics-delete-title"><h3 id="metrics-delete-title">¿Eliminar los datos de demostración?</h3><p>Se borrará el registro temporal de esta página.</p><div className="metrics-actions"><button ref={cancelDelete} className="button secondary" type="button" onClick={() => { restoreFocus.current = true; setConfirming(false); }}>Conservar datos</button><button className="button primary" type="button" onClick={() => { setSaved(null); restoreFocus.current = true; setConfirming(false); setFeedback("Datos de demostración eliminados."); }}>Confirmar eliminación</button></div></section>}
        <dl className="metrics-cards">{metricFields.filter(item => ["impressions", "clicks", "leads", "sales", "revenue", "spend"].includes(item.key)).map(item => <div key={item.key}><dt>{item.label}</dt><dd>{formatMetric(metricValue(saved, item.key), "money" in item ? "currency" : "number")}</dd></div>)}{derived.filter(item => ["CTR", "ROAS"].includes(item.label)).map(item => <div key={item.label}><dt>{item.label}</dt><dd>{formatMetric(item.value, item.format)}</dd></div>)}</dl>
      </section>
      <section className="panel workspace-document"><h2>Detalle de métricas</h2><p>Cálculos matemáticos locales. «—» indica un dato ausente o un cálculo no disponible, incluido un divisor igual a cero.</p><table className="metrics-table"><caption>Datos del periodo · Importes en USD</caption><thead><tr><th scope="col">Métrica</th><th scope="col">Valor</th><th scope="col">Tipo</th></tr></thead><tbody>{metricFields.map(item => <tr key={item.key}><th scope="row">{item.label}</th><td>{formatMetric(metricValue(saved, item.key), "money" in item ? "currency" : "number")}</td><td>Ingresada</td></tr>)}{derived.map(item => <tr key={item.label}><th scope="row">{item.label}<small>{item.formula}</small></th><td>{formatMetric(item.value, item.format)}</td><td>Calculada</td></tr>)}</tbody></table></section>
    </>}
    <section className="panel workspace-document"><h2>Calidad de los datos</h2><span className="example-tag">{dataQuality(saved)}</span><p>Esta evaluación sólo indica si hay datos suficientes para revisar; no determina resultados ni conclusiones.</p><p>Completo para revisión: fechas, impresiones, clics y al menos leads, ventas o ingresos. Parcial: al menos dos métricas sin cumplir lo anterior. Insuficiente: ninguna o una sola métrica. Los ceros cuentan como datos ingresados. Se evalúa el registro guardado.</p></section>
    <div className="workspace-two-grid"><section className="panel workspace-document"><h2>Análisis de rendimiento</h2><span className="example-tag">Pendiente de análisis</span><p>Cuando conectemos el motor de medición, Impulso utilizará estos datos para identificar señales de rendimiento sin convertirlas automáticamente en conclusiones estratégicas.</p></section><section className="panel workspace-document"><h2>Aprendizajes</h2><span className="example-tag">Sin aprendizajes validados</span><p>Los aprendizajes se mostrarán aquí después de que los datos hayan sido evaluados y exista evidencia suficiente.</p></section></div>
  </div>;
}
