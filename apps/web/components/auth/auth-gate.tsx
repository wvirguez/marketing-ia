"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth/auth-context";

/**
 * Client-side route gate. Chosen over Next middleware for this phase:
 * the backend session is an opaque, server-side-hashed token (see
 * apps/api/app/auth/dependencies.py) — there is no way to validate it
 * without a real backend round trip, and checking only for the
 * *presence* of the cookie in middleware would be exactly the kind of
 * fake protection this phase must not implement. A client-side gate
 * backed by `GET /auth/session` (via AuthProvider) is the simplest
 * strategy that is actually correct today, and is also topology-
 * independent: it keeps working if the frontend/backend ever move to
 * different origins where the cookie would not even reach a Next
 * middleware running on the frontend's own server.
 */
export function AuthGate({
  requireStatus,
  redirectTo,
  children,
}: {
  requireStatus: "authenticated" | "unauthenticated";
  redirectTo: string;
  children: React.ReactNode;
}) {
  const auth = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (auth.status !== "loading" && auth.status !== requireStatus) {
      router.replace(redirectTo);
    }
  }, [auth.status, requireStatus, redirectTo, router]);

  // Render nothing but a neutral loading state until the required status
  // is confirmed — this is what avoids a flash of protected content for
  // an unauthenticated visitor (and, symmetrically, a flash of the login
  // form for an already-authenticated one).
  if (auth.status !== requireStatus) {
    return (
      <div className="auth-loading-screen" role="status" aria-live="polite">
        <span className="auth-loading-spinner" aria-hidden="true" />
        <span className="sr-only">Cargando…</span>
      </div>
    );
  }

  return <>{children}</>;
}
