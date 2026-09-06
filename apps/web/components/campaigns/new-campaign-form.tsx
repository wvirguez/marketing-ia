"use client";

import { useRef, useState, type FormEvent } from "react";
import { Icon } from "@/components/ui/icon";
import { CampaignContextFields } from "./campaign-context-fields";
import { ExamplePrompts } from "./example-prompts";
import { CampaignProgress } from "./campaign-progress";

const MAX_LENGTH = 2000;
export function NewCampaignForm() {
  const [idea, setIdea] = useState("");
  const [preparing, setPreparing] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const submitting = useRef(false);
  const valid = idea.trim().length > 0 && idea.length <= MAX_LENGTH;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!valid || submitting.current) return;
    submitting.current = true;
    setPreparing(true);
  }
  function backToIdea() {
    submitting.current = false;
    setPreparing(false);
    window.requestAnimationFrame(() => textarea.current?.focus());
  }

  return (
    <>
      <div hidden={preparing}>
        <form className="campaign-composer panel" onSubmit={handleSubmit}>
          <div className="composer-heading"><span className="composer-symbol"><Icon name="spark" size={24} /></span><div><h2>De una idea a tu próxima campaña</h2><p>No necesitas tener todas las respuestas. Empecemos por lo que imaginas.</p></div></div>
          <label htmlFor="campaign-idea" className="composer-label">¿Qué quieres lanzar?</label>
          <div className="idea-input-wrap"><textarea id="campaign-idea" ref={textarea} value={idea} onChange={event => setIdea(event.target.value.slice(0, MAX_LENGTH))} maxLength={MAX_LENGTH} rows={6} aria-describedby="idea-hint idea-count" placeholder="Ejemplo: Quiero lanzar un ebook sobre adiestramiento de perros en casa para personas que quieren entrenar a su perro desde casa." /><div className="idea-input-footer"><span id="idea-hint"><Icon name="spark" size={14} /> Escríbelo como se lo contarías a tu equipo.</span><span id="idea-count">{idea.length} / {MAX_LENGTH}</span></div></div>
          <CampaignContextFields />
          <div className="composer-footer"><p><span className="demo-pill">DEMO</span> Sólo una simulación. Tu idea no se envía ni se guarda.</p><button type="submit" className="button primary campaign-create" disabled={!valid}><Icon name="spark" size={18} />Crear campaña con IA<Icon name="arrow" size={17} /></button></div>
        </form>
        <ExamplePrompts onSelect={prompt => { setIdea(prompt); textarea.current?.focus(); }} />
        <div className="campaign-reassurance"><Icon name="spark" size={17} /><p>Tú traes la idea. Impulso te ayuda a encontrar el siguiente paso.</p></div>
      </div>
      {preparing && <CampaignProgress idea={idea.trim()} onBack={backToIdea} />}
    </>
  );
}
