import { AuthBrandPanel } from "@/components/auth/auth-brand-panel";
import { LoginForm } from "@/components/auth/login-form";

export default function LoginPage() {
  return (
    <main className="auth-page">
      <a href="#login-form" className="skip-link">Saltar al formulario</a>
      <AuthBrandPanel />
      <section id="login-form" className="auth-form-panel" aria-label="Acceso a tu espacio" tabIndex={-1}>
        <div className="auth-top-note">TU ESPACIO PARA CRECER</div>
        <LoginForm showDemo={process.env.NODE_ENV === "development"} />
        <footer className="auth-form-footer">Impulso. <span>Ideas con dirección.</span></footer>
      </section>
    </main>
  );
}
