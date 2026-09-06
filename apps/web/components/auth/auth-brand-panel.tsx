import { Icon } from "@/components/ui/icon";

export function AuthBrandPanel() {
  return (
    <aside className="auth-brand-panel" aria-label="Acerca de Impulso">
      <div className="brand auth-brand"><span className="brand-symbol"><Icon name="spark" size={26} /></span>impulso<span className="brand-dot">.</span></div>
      <div className="auth-brand-content">
        <span className="auth-eyebrow"><Icon name="spark" size={16} /> MÁS ESPACIO PARA TUS IDEAS</span>
        <h2>Tu agencia de marketing,{" "}<br /><span>potenciada por inteligencia artificial.</span></h2>
        <p>Planifica, crea, ejecuta y optimiza tus campañas desde un solo lugar.</p>
        <div className="auth-illustration" aria-hidden="true">
          <div className="auth-orbit" /><div className="auth-orbit outer" />
          <div className="auth-idea-core"><Icon name="spark" size={48} /></div>
          <div className="auth-float idea-note"><span className="icon-tile blue"><Icon name="campaign" /></span><div><strong>Todo empieza con una idea</strong><span>Tu próxima campaña, más cerca</span></div></div>
          <div className="auth-float plan-note"><span className="icon-tile mint"><Icon name="check" /></span><div><strong>Una visión. Un solo espacio.</strong><span>Estrategia, contenido y creatividad</span></div></div>
          <span className="auth-art-star">✦</span>
        </div>
      </div>
      <p className="auth-brand-footer">Tu creatividad, más lejos. <Icon name="spark" size={15} /></p>
    </aside>
  );
}
