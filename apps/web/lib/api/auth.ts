// Typed service functions for the Auth/Users/Workspaces contracts
// consumed by MVP-02. Every shape here is read directly from the actual
// backend routers/schemas (apps/api/app/auth, app/users, app/workspaces)
// — no speculative fields.

import { fetchCsrfToken, request } from "@/lib/api/client";
import type {
  CsrfTokenResponse,
  LoginRequest,
  LogoutResponse,
  RegisterRequest,
  SessionContext,
  UserPublic,
  WorkspacePublic,
} from "@/types/auth";

export async function register(payload: RegisterRequest): Promise<SessionContext> {
  return request<SessionContext>("/auth/register", { method: "POST", body: payload, skipCsrf: true });
}

export async function login(payload: LoginRequest): Promise<SessionContext> {
  return request<SessionContext>("/auth/login", { method: "POST", body: payload, skipCsrf: true });
}

export async function logout(): Promise<LogoutResponse> {
  return request<LogoutResponse>("/auth/logout", { method: "POST" });
}

/** `GET /auth/session` — the single-round-trip session-restore/bootstrap call: it already returns user + workspace + membership together. */
export async function getSession(): Promise<SessionContext> {
  return request<SessionContext>("/auth/session", { method: "GET" });
}

export async function getCsrfToken(): Promise<CsrfTokenResponse["csrf_token"]> {
  return fetchCsrfToken();
}

/** `GET /users/me` — kept available for later phases (e.g. Settings/Profile); not used by the MVP-02 bootstrap, which sources the same data from `getSession()` in one round trip. */
export async function getCurrentUser(): Promise<UserPublic> {
  return request<UserPublic>("/users/me", { method: "GET" });
}

/** `GET /workspaces/current` — kept available for later phases; MVP-02's bootstrap sources the same data from `getSession()`. */
export async function getCurrentWorkspace(): Promise<WorkspacePublic> {
  return request<WorkspacePublic>("/workspaces/current", { method: "GET" });
}
