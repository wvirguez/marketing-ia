import { ApiError } from "@/lib/api/client";

/** Maps a backend error code (apps/api/app/core/api_errors.py) to a safe, user-facing Spanish message for Campaign screens. Mirrors lib/auth/error-messages.ts's shape, scoped to the codes Campaign routes can actually raise. */
export function describeCampaignError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "FORBIDDEN":
        return "No encontramos esta campaña, o no tienes acceso a ella.";
      case "VALIDATION_ERROR":
        return error.details?.[0]?.message ?? "Revisa los datos del formulario.";
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
