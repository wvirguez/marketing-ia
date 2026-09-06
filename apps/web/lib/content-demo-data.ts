import type { CarouselDetail, CarouselSlide, ContentFormat, ReelDetail, ReelScene, StoryDetail, StorySlide, StorySlideKind } from "@/types/content-detail";

// Representative demo asset per format. Every "Ver contenido" action in the
// Campaign Workspace links to one of these three fixed examples.
export const contentDetailSlugByFormat: Record<ContentFormat, string> = {
  Reel: "reel-01",
  Carrusel: "carousel-01",
  Story: "story-01",
};

export const storyKindLabel: Record<StorySlideKind, string> = {
  question: "Pregunta",
  poll: "Encuesta",
  tip: "Tip",
  cta: "CTA",
};

const reelHook = "Si tu perro parece ignorarte, quizá el problema no sea que no quiere obedecer.";
const reelScenes: ReelScene[] = [
  { number: 1, visual: "Plano cercano del dueño dando una orden mientras el perro mira hacia otro lado, ligeramente confundido.", onScreenText: "¿Tu perro te ignora?", narration: "Si tu perro parece ignorarte, quizá el problema no sea que no quiere obedecer." },
  { number: 2, visual: "Corte a la misma escena: el dueño repite la orden usando palabras distintas cada vez.", onScreenText: "\"Siéntate\"... \"Sienta\"... \"Quieto, siéntate\"", narration: "Muchas veces cambiamos la palabra, el tono o el gesto sin darnos cuenta." },
  { number: 3, visual: "Primer plano del perro: orejas atentas pero sin reaccionar, transmitiendo indecisión.", onScreenText: "Para él, cada variación es una orden distinta.", narration: "Para tu perro, cada variación puede sonar como una instrucción completamente nueva." },
  { number: 4, visual: "El dueño se detiene, respira y da la orden una sola vez, con una palabra y un gesto fijos.", onScreenText: "Una palabra. Un gesto. Siempre igual.", narration: "La claridad y la consistencia son la base de cualquier aprendizaje." },
  { number: 5, visual: "El perro se sienta correctamente; el dueño sonríe y lo recompensa de inmediato.", onScreenText: "Guarda este consejo", narration: "Guarda este consejo y prueba una instrucción más clara hoy." },
];
const reelCaption = "Si sientes que tu perro no te hace caso, antes de pensar en terquedad revisa la claridad de tu instrucción 🐶. Usar siempre la misma palabra, el mismo gesto y el mismo tono ayuda a que tu perro entienda exactamente qué le estás pidiendo. Guarda este consejo y ponlo en práctica en tu próxima sesión de entrenamiento en casa.";
const reelHashtags = ["#adiestramientocanino", "#perros", "#educacioncanina", "#entrenamientocanino", "#mascotas"];
const reelCta = "Guarda este consejo y prueba una instrucción más clara hoy.";

export const reelDetail: ReelDetail = {
  kind: "reel",
  slug: "reel-01",
  title: "Tu perro no te ignora: puede que la instrucción no esté clara",
  format: "Reel",
  status: "Listo para revisión",
  objective: "Educación / reconocimiento del problema",
  funnelStage: "TOFU",
  cta: "Guarda este consejo",
  channel: "Instagram",
  hook: reelHook,
  scenes: reelScenes,
  caption: reelCaption,
  hashtags: reelHashtags,
  copy: {
    script: reelScenes.map(scene => `Escena ${scene.number}\nVisual: ${scene.visual}\nTexto en pantalla: ${scene.onScreenText}\nNarración: ${scene.narration}`).join("\n\n"),
    caption: `${reelCaption}\n\n${reelHashtags.join(" ")}`,
    cta: reelCta,
  },
};

const carouselTitle = "3 errores comunes al enseñar una orden";
const carouselSlides: CarouselSlide[] = [
  { number: 1, title: carouselTitle, body: "Antes de dudar de tu perro, revisa si estás cometiendo alguno de estos errores frecuentes al enseñar una instrucción básica.", visualObjective: "Portada con titular grande sobre fondo degradado navy y azul eléctrico, símbolo de marca visible; debe transmitir autoridad y generar curiosidad para deslizar." },
  { number: 2, title: "Repetir la orden demasiadas veces", body: "Decir \"siéntate, siéntate, siéntate\" le enseña a tu perro que puede esperar antes de reaccionar.", visualObjective: "Ícono de repetición o burbujas de texto duplicadas, con un tono de advertencia suave." },
  { number: 3, title: "Usar palabras diferentes para lo mismo", body: "Alternar entre \"quieto\", \"siéntate\" o \"para\" genera confusión sobre qué comportamiento esperas.", visualObjective: "Varias palabras distintas apuntando a la misma acción, reforzando la idea de confusión." },
  { number: 4, title: "Premiar demasiado tarde", body: "Si el premio llega segundos después del comportamiento correcto, tu perro no logra asociarlo con la acción.", visualObjective: "Línea de tiempo mostrando el momento ideal para premiar frente a un premio tardío." },
  { number: 5, title: "Empieza con una instrucción clara y consistente", body: "Elige una palabra, un gesto y un momento para premiar, y mantenlos siempre igual.", visualObjective: "Slide de cierre con checklist visual sobre fondo cian, con tono de solución y cierre positivo." },
];
const carouselFinalCta = "Guarda este carrusel para tu próxima práctica";
const carouselCaption = "Enseñar una orden no depende de repetir más, sino de ser más claro. Guarda este carrusel y evita estos 3 errores comunes en tu próxima sesión de práctica en casa. 🐾";

export const carouselDetail: CarouselDetail = {
  kind: "carousel",
  slug: "carousel-01",
  title: carouselTitle,
  format: "Carrusel",
  status: "Listo para revisión",
  objective: "Educación / prevención de errores comunes",
  funnelStage: "TOFU",
  cta: carouselFinalCta,
  channel: "Instagram",
  slides: carouselSlides,
  finalCta: carouselFinalCta,
  copy: {
    script: carouselSlides.map(slide => `${slide.number === 1 ? "Portada" : `Diapositiva ${slide.number}`}: ${slide.title}\n${slide.body}`).join("\n\n"),
    caption: carouselCaption,
    cta: carouselFinalCta,
  },
};

const storyTitle = "¿Tu perro responde a la primera?";
const storySlides: StorySlide[] = [
  { number: 1, kind: "question", title: storyTitle, body: "Antes de seguir, piensa en la última vez que le diste una orden a tu perro." },
  { number: 2, kind: "poll", title: "Vota rápido", body: "¿Tu perro responde a la primera?", pollOptions: ["Sí", "A veces", "Casi nunca"] },
  { number: 3, kind: "tip", title: "Un tip rápido", body: "Usa siempre la misma palabra y el mismo gesto: la consistencia acelera el aprendizaje." },
  { number: 4, kind: "cta", title: "Guarda este consejo", body: "Desliza hacia arriba y descubre más tips como este en el perfil." },
];
const storyCta = "Guarda este consejo y sigue aprendiendo con nosotros.";
const storyCaption = "¿Tu perro responde a la primera? Recorre esta story para descubrir un tip rápido y cuéntanos en la encuesta.";

export const storyDetail: StoryDetail = {
  kind: "story",
  slug: "story-01",
  title: storyTitle,
  format: "Story",
  status: "Listo para revisión",
  objective: "Interacción / validación de la audiencia",
  funnelStage: "TOFU",
  cta: "Participa en la encuesta",
  channel: "Instagram",
  slides: storySlides,
  copy: {
    script: storySlides.map(slide => `Story ${slide.number} · ${storyKindLabel[slide.kind]}: ${slide.title}${slide.body ? `\n${slide.body}` : ""}${slide.pollOptions ? `\nOpciones: ${slide.pollOptions.join(" / ")}` : ""}`).join("\n\n"),
    caption: storyCaption,
    cta: storyCta,
  },
};
