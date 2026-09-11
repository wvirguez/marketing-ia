"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { getSession, login as loginRequest, logout as logoutRequest, register as registerRequest } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import type { LoginRequest, RegisterRequest, SessionContext } from "@/types/auth";

// The backend session is the sole source of truth; client auth state is
// only ever a cached representation of it (MVP-02R). These are the two
// backend error codes that PROVE, authoritatively, that no valid session
// exists any more (apps/api/app/auth/dependencies.py::get_current_session)
// — every other logout failure (CSRF, network, 5xx, anything else) proves
// nothing about the server session and must never be treated as a logout.
const SESSION_ALREADY_GONE_CODES = new Set(["AUTHENTICATION_REQUIRED", "SESSION_EXPIRED"]);

type AuthState =
  | { status: "loading" }
  | { status: "unauthenticated" }
  | { status: "authenticated"; session: SessionContext };

interface AuthActions {
  login: (payload: LoginRequest) => Promise<void>;
  register: (payload: RegisterRequest) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

export type AuthContextValue = AuthState & AuthActions;

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });

  // Reload must never assume a React state variable is still true — the
  // only source of truth is the backend re-checking the HttpOnly session
  // cookie itself (`GET /auth/session`). Bootstrap is inlined directly in
  // the effect (rather than calling out to `refresh` below) with a
  // mount-guard, the standard "fetch on mount" shape, so a fast
  // unmount/remount (React 19 Strict Mode) can never apply a stale result.
  useEffect(() => {
    let active = true;
    getSession()
      .then((session) => {
        if (active) setState({ status: "authenticated", session });
      })
      .catch(() => {
        if (active) setState({ status: "unauthenticated" });
      });
    return () => {
      active = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const session = await getSession();
      setState({ status: "authenticated", session });
    } catch {
      setState({ status: "unauthenticated" });
    }
  }, []);

  const login = useCallback(async (payload: LoginRequest) => {
    const session = await loginRequest(payload);
    setState({ status: "authenticated", session });
  }, []);

  const register = useCallback(async (payload: RegisterRequest) => {
    const session = await registerRequest(payload);
    setState({ status: "authenticated", session });
  }, []);

  // Server-authoritative logout (MVP-02R repair): local state becomes
  // `unauthenticated` only when the backend confirms the session is
  // actually gone — either a genuine `200` from `POST /auth/logout`, or
  // an authoritative `401` proving no valid session exists any more. Any
  // other failure (CSRF, network, 5xx, unknown) leaves `authenticated`
  // state untouched and rethrows, so the caller can surface it and let
  // the user retry without the frontend fabricating a server-side
  // session transition that never happened.
  const logout = useCallback(async () => {
    try {
      await logoutRequest();
      setState({ status: "unauthenticated" });
    } catch (error) {
      if (error instanceof ApiError && SESSION_ALREADY_GONE_CODES.has(error.code)) {
        setState({ status: "unauthenticated" });
        return;
      }
      throw error;
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ ...state, login, register, logout, refresh }),
    [state, login, register, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider.");
  }
  return context;
}
