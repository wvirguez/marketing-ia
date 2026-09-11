import { AuthBrandPanel } from "@/components/auth/auth-brand-panel";
import { AuthGate } from "@/components/auth/auth-gate";
import { RegisterForm } from "@/components/auth/register-form";

export default function RegisterPage() {
  return (
    <AuthGate requireStatus="unauthenticated" redirectTo="/">
      <main className="auth-page">
        <a href="#register-form" className="skip-link">Saltar al formulario</a>
        <AuthBrandPanel />
        <section id="register-form" className="auth-form-panel" aria-label="Crear tu cuenta" tabIndex={-1}>
          <div className="auth-top-note">TU ESPACIO PARA CRECER</div>
          <RegisterForm />
          <footer className="auth-form-footer">Impulso. <span>Ideas con dirección.</span></footer>
        </section>
      </main>
    </AuthGate>
  );
}
