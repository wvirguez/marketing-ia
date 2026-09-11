import { ApiError } from "@/lib/api/client";

/** Maps a backend error code (apps/api/app/core/api_errors.py) to a safe, user-facing Spanish message. Never surfaces `error.message` from a 5xx/network failure verbatim, and never a raw stack trace. */
export function describeAuthError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "INVALID_CREDENTIALS":
        return "Correo o contraseña incorrectos.";
      case "EMAIL_ALREADY_REGISTERED":
        return "Ya existe una cuenta con este correo.";
      case "VALIDATION_ERROR":
        return error.details?.[0]?.message ?? "Revisa los datos ingresados.";
      case "AUTHENTICATION_REQUIRED":
      case "SESSION_EXPIRED":
        return "Tu sesión ha expirado. Inicia sesión de nuevo.";
      case "CSRF_INVALID":
        return "Tu sesión cambió. Intenta de nuevo.";
      case "NETWORK_ERROR":
        return "No pudimos conectar con el servidor. Verifica tu conexión.";
      case "CONFIG_ERROR":
        return "La aplicación no está configurada correctamente. Contacta a soporte.";
      default:
        return error.status >= 500
          ? "Ocurrió un error inesperado. Intenta de nuevo en unos minutos."
          : "No pudimos completar esta acción. Intenta de nuevo.";
    }
  }
  return "Ocurrió un error inesperado. Intenta de nuevo.";
}
