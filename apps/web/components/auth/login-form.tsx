"use client";

import Link from "next/link";
import { useState, useSyncExternalStore, type FormEvent } from "react";
import { Icon } from "@/components/ui/icon";

const subscribe = () => () => {};
const clientReady = () => true;
const serverReady = () => false;

export function LoginForm({ showDemo }: { showDemo: boolean }) {
  const [visible, setVisible] = useState(false);
  const [message, setMessage] = useState("");
  // Keep native submission unavailable until the client interception is ready.
  const hydrated = useSyncExternalStore(subscribe, clientReady, serverReady);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("La autenticación estará disponible cuando conectemos el backend.");
  }

  return (
    <div className="login-card">
      <span className="login-welcome-icon"><Icon name="spark" size={24} /></span>
      <h1>Bienvenido de nuevo</h1>
      <p className="login-subtitle">Ingresa a tu espacio de trabajo.</p>
      <div className="login-preview-note"><span className="demo-pill">VISTA PREVIA</span> La autenticación aún no está disponible.</div>
      <form onSubmit={handleSubmit} aria-label="Inicio de sesión" aria-describedby="login-message" noValidate>
        <fieldset disabled={!hydrated} className="login-fields">
          <legend className="sr-only">Datos de acceso</legend>
          <div className="login-field">
            <label htmlFor="login-email">Correo electrónico</label>
            <input id="login-email" type="email" autoComplete="username" placeholder="tu@agencia.com" autoCapitalize="none" spellCheck={false} />
          </div>
          <div className="login-field">
            <label htmlFor="login-password">Contraseña</label>
            <div className="password-input">
              <input id="login-password" type={visible ? "text" : "password"} autoComplete="current-password" placeholder="Ingresa tu contraseña" />
              <button type="button" className="password-toggle" aria-label={visible ? "Ocultar contraseña" : "Mostrar contraseña"} aria-pressed={visible} aria-controls="login-password" onClick={() => setVisible(!visible)}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" />{visible && <path d="m3 3 18 18" />}</svg>
              </button>
            </div>
          </div>
          <div className="login-options">
            <label className="remember-label"><input type="checkbox" aria-describedby="remember-note" />Recordarme</label>
            <button type="button" className="auth-text-button" aria-disabled="true" title="Recuperación disponible próximamente">¿Olvidaste tu contraseña?</button>
          </div>
          <p id="remember-note" className="sr-only">Opción visual. No se guardará tu sesión.</p>
          <button type="submit" className="button primary login-submit">Iniciar sesión<Icon name="arrow" size={18} /></button>
        </fieldset>
        <p id="login-message" className="login-message" role="status" aria-live="polite" aria-atomic="true">{message}</p>
        <noscript><p className="login-noscript">Activa JavaScript para probar esta vista. La autenticación todavía no está disponible.</p></noscript>
      </form>
      <div className="login-separator"><span />o<span /></div>
      <button type="button" className="google-button" aria-disabled="true" title="Google Login disponible próximamente"><span className="google-mark" aria-hidden="true">G</span>Continuar con Google<span className="auth-soon">Próximamente</span></button>
      <p className="login-signup">¿No tienes una cuenta? <button type="button" className="auth-text-button" aria-disabled="true" title="Registro disponible próximamente">Crear cuenta</button></p>
      {showDemo && <div className="login-demo"><span className="demo-pill">DEMO · DESARROLLO</span><Link href="/" prefetch={false}>Entrar al dashboard de demostración <Icon name="arrow" size={16} /></Link><p>Explora con datos de ejemplo, sin iniciar sesión.</p></div>}
    </div>
  );
}
