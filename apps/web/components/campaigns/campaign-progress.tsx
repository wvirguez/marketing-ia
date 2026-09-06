import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/icon";

const steps = ["Entendiendo tu idea", "Analizando mercado", "Identificando audiencia", "Diseñando estrategia", "Planificando contenido", "Preparando creatividades", "Preparando medición"];

export function CampaignProgress({ idea, onBack }: { idea: string; onBack: () => void }) {
  const [completed, setCompleted] = useState(1);
  const title = useRef<HTMLHeadingElement>(null);
  const done = completed === steps.length;

  useEffect(() => { title.current?.focus(); }, []);
  useEffect(() => {
    if (done) return;
    const timer = window.setTimeout(() => setCompleted(count => count + 1), 900);
    return () => window.clearTimeout(timer);
  }, [completed, done]);

  return (
    <section className="campaign-preparation panel" aria-labelledby="preparation-title">
      <span className={`preparation-symbol ${done ? "finished" : ""}`}><Icon name={done ? "check" : "spark"} size={29} /></span>
      <span className="demo-pill">SIMULACIÓN · SIN GENERACIÓN REAL</span>
      <h2 id="preparation-title" ref={title} tabIndex={-1}>{done ? "Tu campaña está lista para configurar" : "Tu idea empieza a tomar forma"}</h2>
      <p className="preparation-description">{done ? "Has completado la vista previa. No se ha creado ni guardado una campaña real." : "Así se verá la preparación de tu campaña. Por ahora, todos los pasos son de demostración."}</p>
      <blockquote>{idea}</blockquote>
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">{done ? "Tu campaña está lista para configurar. Simulación completada." : `Simulación: ${steps[completed]}. ${completed} de ${steps.length} pasos completados.`}</p>
      <div className="preparation-progress"><span>{done ? "Vista previa completada" : "Preparando tu campaña"}</span><span>{completed} / {steps.length}</span></div>
      <progress value={completed} max={steps.length} aria-label="Avance de la simulación" />
      <ol className="preparation-steps">{steps.map((step, index) => {
        const state = index < completed ? "done" : index === completed ? "current" : "pending";
        return <li key={step} className={state} aria-current={state === "current" ? "step" : undefined}><span className="activity-marker" aria-hidden="true">{state === "done" ? <Icon name="check" size={14} /> : state === "current" ? "●" : "○"}</span><span>{step}<span className="sr-only">: {state === "done" ? "completado" : state === "current" ? "en curso" : "pendiente"}</span></span></li>;
      })}</ol>
      {done && <div className="preparation-next"><button type="button" className="button primary" disabled aria-describedby="campaign-next-note">Ver campaña <Icon name="arrow" size={16} /></button><p id="campaign-next-note">Próximo paso · El espacio de campaña estará disponible más adelante.</p></div>}
      <button type="button" className="auth-text-button preparation-back" onClick={onBack}>{done ? "Volver a mi idea" : "Cancelar simulación y volver"}</button>
    </section>
  );
}
