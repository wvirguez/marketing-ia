import type { CampaignWorkspace, ContentItem, WorkspaceTab } from "@/types/campaign-workspace";

// Fixed presentation fixture, independent of the new-campaign prompt.
export const workspaceTabs: { id: WorkspaceTab; label: string }[] = [
  { id: "overview", label: "Resumen" }, { id: "research", label: "Investigación" },
  { id: "audience", label: "Audiencia" }, { id: "strategy", label: "Estrategia" },
  { id: "plan", label: "Plan" }, { id: "content", label: "Contenido" },
  { id: "creatives", label: "Creatividades" }, { id: "paid", label: "Paid Media" },
  { id: "tracking", label: "Tracking" }, { id: "metrics", label: "Métricas" },
];
export const campaignDemo: CampaignWorkspace = {
  name: "Método Canino en Casa",
  description: "Curso online para ayudar a propietarios a mejorar progresivamente la obediencia de su perro desde casa.",
  product: "Curso online", price: "USD 97", channel: "Instagram", status: "En preparación", progress: 65,
  highlights: [
    { label: "Objetivo principal", value: "Validar interés y generar ventas iniciales del curso." },
    { label: "Audiencia", value: "Propietarios que quieren mejorar la obediencia básica de su perro desde casa." },
    { label: "Mensaje central", value: "Un método práctico y progresivo para mejorar la obediencia sin complicaciones." },
    { label: "Oferta", value: "Curso online — USD 97" },
  ],
  workflow: [
    { label: "Idea comprendida", state: "done" }, { label: "Mercado analizado", state: "done" },
    { label: "Audiencia identificada", state: "done" }, { label: "Estrategia creada", state: "done" },
    { label: "Plan de contenido preparado", state: "done" }, { label: "Contenido en producción", state: "current" },
    { label: "Creatividades pendientes", state: "pending" }, { label: "Tracking pendiente", state: "pending" },
  ],
  deliverables: [
    { title: "Investigación de mercado", status: "Completado", tab: "research" },
    { title: "Perfil de audiencia", status: "Completado", tab: "audience" },
    { title: "Estrategia", status: "Completado", tab: "strategy" },
    { title: "Plan de contenido", status: "Completado", tab: "plan" },
    { title: "Posts", status: "En producción", tab: "content" },
    { title: "Guiones de Reel", status: "En producción", tab: "content" },
    { title: "Creatividades", status: "Pendiente", tab: "creatives" },
    { title: "Paid Media Plan", status: "Pendiente", tab: "paid" },
    { title: "Tracking Plan", status: "Pendiente", tab: "tracking" },
  ],
};
export const researchPatterns = ["Muchos cursos prometen obediencia rápida.", "Existe interés por métodos fáciles de aplicar desde casa.", "Los principiantes expresan dudas sobre qué hacer primero."];
export const audienceGroups = [
  { title: "Problemas", items: ["No saben por dónde empezar.", "El perro responde sólo cuando hay premios.", "Les cuesta aplicar lo aprendido en vídeos."] },
  { title: "Deseos", items: ["Instrucciones claras.", "Progreso visible.", "Un método fácil de seguir."] },
  { title: "Objeciones", items: ["Miedo a que no funcione.", "Falta de tiempo.", "Dudas sobre su propia capacidad para entrenar."] },
];
export const strategy = [
  { label: "Objetivo", value: "Validar demanda inicial y generar primeras ventas." },
  { label: "Posicionamiento", value: "Una alternativa práctica y progresiva para propietarios que quieren entrenar desde casa." },
  { label: "Mensaje central", value: campaignDemo.highlights[2].value },
  { label: "Funnel stage", value: "Descubrimiento → consideración → conversión inicial." },
  { label: "CTA principal", value: "Conoce el método" },
  { label: "Hipótesis de campaña", value: "Los mensajes centrados en claridad y progresión generarán mayor interés que las promesas de rapidez." },
];
export const demoContent: ContentItem[] = [
  { id: "reel-1", title: "Tu perro no te ignora: puede que la instrucción no esté clara", format: "Reel", objective: "Generar identificación y despertar interés.", status: "En producción", cta: "Conoce el método", week: 1 },
  { id: "carousel-1", title: "3 errores comunes al enseñar una orden", format: "Carrusel", objective: "Aportar claridad sobre los primeros pasos.", status: "En producción", cta: "Guarda estos consejos", week: 1 },
  { id: "story-1", title: "Encuesta: ¿qué orden le cuesta más a tu perro?", format: "Story", objective: "Conocer las inquietudes de la audiencia.", status: "Planificado", cta: "Participa en la encuesta", week: 1 },
  { id: "reel-2", title: "Una instrucción clara, un pequeño avance", format: "Reel", objective: "Explicar el enfoque progresivo del curso.", status: "Planificado", cta: "Descubre cómo empezar", week: 2 },
  { id: "carousel-2", title: "Tu primera semana de práctica en casa", format: "Carrusel", objective: "Presentar una rutina fácil de seguir.", status: "Pendiente", cta: "Conoce el método", week: 2 },
  { id: "story-2", title: "El siguiente paso para entrenar en casa", format: "Story", objective: "Invitar a descubrir la propuesta.", status: "Pendiente", cta: "Conoce el método", week: 2 },
];
export const demoCreatives = [
  { title: "Una idea clara, un pequeño avance", format: "Carrusel", size: "1080x1350", status: "Lista", tone: "navy" },
  { title: "Aprender juntos, desde casa", format: "Reel", size: "1080x1920", status: "Pendiente", tone: "cyan" },
  { title: "Tu próxima práctica empieza aquí", format: "Story", size: "1080x1920", status: "Pendiente", tone: "light" },
];
export const trackingChecklist = [
  { label: "Landing page", status: "Pendiente" }, { label: "Checkout", status: "No disponible" },
  { label: "Purchase event", status: "Pendiente" }, { label: "UTM structure", status: "Configurado" },
  { label: "Analytics", status: "No disponible" }, { label: "Conversion tracking", status: "Pendiente" },
];
