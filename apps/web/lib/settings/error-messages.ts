import { ApiError } from "@/lib/api/client";

/** Maps a backend error code (apps/api/app/core/api_errors.py) to a safe,
 * user-facing Spanish message for Settings screens. Mirrors
 * lib/campaigns/error-messages.ts's/lib/auth/error-messages.ts's shape,
 * scoped to the codes Settings routes (`/workspaces/{id}/settings`,
 * `/users/me`) can actually raise. `FORBIDDEN` here covers both the
 * non-leaky tenant gate (unknown/foreign workspace) and the role-gate
 * (a MEMBER submitting an Admin-only section) — the backend does not
 * distinguish the two by code, so neither does this copy. */
export function describeSettingsError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "FORBIDDEN":
        return "No tienes permiso para modificar esta sección, o no tienes acceso a este espacio de trabajo.";
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
