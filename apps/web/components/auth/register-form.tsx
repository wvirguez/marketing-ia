"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore, type FormEvent } from "react";
import { Icon } from "@/components/ui/icon";
import { useAuth } from "@/lib/auth/auth-context";
import { describeAuthError } from "@/lib/auth/error-messages";

const subscribe = () => () => {};
const clientReady = () => true;
const serverReady = () => false;

export function RegisterForm() {
  const auth = useAuth();
  const router = useRouter();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const hydrated = useSyncExternalStore(subscribe, clientReady, serverReady);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    setSubmitting(true);
    try {
      // organization_name/workspace_name are intentionally omitted: the
      // backend already derives sensible defaults from display_name
      // (see AuthService.register) — inventing form fields for them
      // here would duplicate that logic on the frontend.
      await auth.register({ email, password, display_name: displayName });
      router.replace("/");
    } catch (error) {
      setMessage(describeAuthError(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-card">
      <span className="login-welcome-icon"><Icon name="spark" size={24} /></span>
      <h1>Crea tu cuenta</h1>
      <p className="login-subtitle">Empieza tu espacio de trabajo en Impulso.</p>
      <form onSubmit={handleSubmit} aria-label="Crear cuenta" aria-describedby="register-message" noValidate>
        <fieldset disabled={!hydrated || submitting} className="login-fields">
          <legend className="sr-only">Datos de la cuenta</legend>
          <div className="login-field">
            <label htmlFor="register-name">Nombre completo</label>
            <input
              id="register-name"
              type="text"
              autoComplete="name"
              placeholder="Tu nombre"
              required
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </div>
          <div className="login-field">
            <label htmlFor="register-email">Correo electrónico</label>
            <input
              id="register-email"
              type="email"
              autoComplete="username"
              placeholder="tu@agencia.com"
              autoCapitalize="none"
              spellCheck={false}
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <div className="login-field">
            <label htmlFor="register-password">Contraseña</label>
            <div className="password-input">
              <input
                id="register-password"
                type={visible ? "text" : "password"}
                autoComplete="new-password"
                placeholder="Mínimo 8 caracteres"
                minLength={8}
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
              <button type="button" className="password-toggle" aria-label={visible ? "Ocultar contraseña" : "Mostrar contraseña"} aria-pressed={visible} aria-controls="register-password" onClick={() => setVisible(!visible)}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" />{visible && <path d="m3 3 18 18" />}</svg>
              </button>
            </div>
          </div>
          <button type="submit" className="button primary login-submit">{submitting ? "Creando cuenta…" : "Crear cuenta"}{!submitting && <Icon name="arrow" size={18} />}</button>
        </fieldset>
        <p id="register-message" className="login-message" role="status" aria-live="polite" aria-atomic="true">{message}</p>
        <noscript><p className="login-noscript">Activa JavaScript para usar esta vista.</p></noscript>
      </form>
      <p className="login-signup">¿Ya tienes una cuenta? <Link href="/login" className="auth-text-button">Iniciar sesión</Link></p>
    </div>
  );
}
