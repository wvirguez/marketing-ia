import { Icon, type IconName } from "@/components/ui/icon";

const examples: { category: string; prompt: string; icon: IconName; tone: string }[] = [
  { category: "UN EBOOK", prompt: "Quiero lanzar un ebook sobre adiestramiento de perros en casa", icon: "content", tone: "blue" },
  { category: "UNA CONSULTORÍA", prompt: "Quiero vender una consultoría de marketing para pequeñas empresas", icon: "chart", tone: "violet" },
  { category: "UN CURSO ONLINE", prompt: "Quiero lanzar un curso online de finanzas personales", icon: "image", tone: "mint" },
  { category: "MI AGENCIA", prompt: "Quiero crear una campaña para conseguir clientes para mi agencia", icon: "campaign", tone: "orange" },
];
export function ExamplePrompts({ onSelect }: { onSelect: (prompt: string) => void }) {
  return (
    <section className="campaign-examples" aria-labelledby="examples-title">
      <div className="section-heading"><h2 id="examples-title">Prueba con un ejemplo</h2><span className="muted small-text">Una idea es suficiente para empezar</span></div>
      <div className="example-grid">{examples.map(example => (
        <button type="button" className="example-card" key={example.category} onClick={() => onSelect(example.prompt)}>
          <span className={`icon-tile ${example.tone}`}><Icon name={example.icon} /></span>
          <span className="example-category">{example.category}</span>
          <span className="example-prompt">{example.prompt}</span>
          <span className="example-use">Usar ejemplo <Icon name="arrow" size={15} /></span>
        </button>
      ))}</div>
    </section>
  );
}
