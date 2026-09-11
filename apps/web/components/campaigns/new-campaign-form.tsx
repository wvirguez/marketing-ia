"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import { Icon } from "@/components/ui/icon";
import { createCampaign } from "@/lib/api/campaigns";
import { describeCampaignError } from "@/lib/campaigns/error-messages";
import { CampaignContextFields, EMPTY_CAMPAIGN_CONTEXT, type CampaignContextValues } from "./campaign-context-fields";
import { ExamplePrompts } from "./example-prompts";

const IDEA_MAX_LENGTH = 2000;
const NAME_MAX_LENGTH = 255;

export function NewCampaignForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [idea, setIdea] = useState("");
  const [context, setContext] = useState<CampaignContextValues>(EMPTY_CAMPAIGN_CONTEXT);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);
  const valid = name.trim().length > 0 && idea.trim().length > 0 && idea.length <= IDEA_MAX_LENGTH;

  function updateContext<K extends keyof CampaignContextValues>(field: K, value: CampaignContextValues[K]) {
    setContext((previous) => ({ ...previous, [field]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!valid || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const response = await createCampaign({
        name: name.trim(),
        prompt: idea.trim(),
        product_type: context.productType || null,
        price: context.price || null,
        audience: context.audience || null,
        budget: context.budget || null,
        channel: context.channel || null,
      });
      router.push(`/campaigns/${response.campaign.id}`);
    } catch (submitError) {
      setError(describeCampaignError(submitError));
      setSubmitting(false);
    }
  }

  return (
    <>
      <form className="campaign-composer panel" onSubmit={handleSubmit}>
        <div className="composer-heading"><span className="composer-symbol"><Icon name="spark" size={24} /></span><div><h2>De una idea a tu próxima campaña</h2><p>No necesitas tener todas las respuestas. Empecemos por lo que imaginas.</p></div></div>
        <fieldset disabled={submitting} className="login-fields">
          <legend className="sr-only">Datos de la campaña</legend>
          <label htmlFor="campaign-name" className="composer-label">Nombre de la campaña</label>
          <div className="login-field"><input id="campaign-name" type="text" maxLength={NAME_MAX_LENGTH} value={name} onChange={(event) => setName(event.target.value)} placeholder="Ej. Método Canino en Casa" required /></div>
          <label htmlFor="campaign-idea" className="composer-label">¿Qué quieres lanzar?</label>
          <div className="idea-input-wrap"><textarea id="campaign-idea" ref={textarea} value={idea} onChange={event => setIdea(event.target.value.slice(0, IDEA_MAX_LENGTH))} maxLength={IDEA_MAX_LENGTH} rows={6} aria-describedby="idea-hint idea-count" placeholder="Ejemplo: Quiero lanzar un ebook sobre adiestramiento de perros en casa para personas que quieren entrenar a su perro desde casa." /><div className="idea-input-footer"><span id="idea-hint"><Icon name="spark" size={14} /> Escríbelo como se lo contarías a tu equipo.</span><span id="idea-count">{idea.length} / {IDEA_MAX_LENGTH}</span></div></div>
          <CampaignContextFields values={context} onChange={updateContext} />
          <div className="composer-footer"><p role="status" aria-live="polite" aria-atomic="true">{error}</p><button type="submit" className="button primary campaign-create" disabled={!valid || submitting}><Icon name="spark" size={18} />{submitting ? "Creando campaña…" : "Crear campaña"}{!submitting && <Icon name="arrow" size={17} />}</button></div>
        </fieldset>
      </form>
      <ExamplePrompts onSelect={prompt => { setIdea(prompt); textarea.current?.focus(); }} />
      <div className="campaign-reassurance"><Icon name="spark" size={17} /><p>Tú traes la idea. Impulso te ayuda a encontrar el siguiente paso.</p></div>
    </>
  );
}
