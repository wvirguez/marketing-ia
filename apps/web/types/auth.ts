// Mirrors apps/api/app/auth/schemas.py, app/users/schemas.py, and
// app/workspaces/schemas.py exactly. No speculative fields: every field
// here corresponds to an actual backend response/request field.

export interface UserPreferencesPublic {
  locale: string | null;
  timezone: string | null;
}

export interface UserPublic {
  id: string;
  email: string;
  display_name: string;
  status: string;
  preferences: UserPreferencesPublic;
}

export interface WorkspacePublic {
  id: string;
  name: string;
  slug: string;
}

export interface MembershipPublic {
  role: string;
}

export interface SessionContext {
  user: UserPublic;
  workspace: WorkspacePublic;
  membership: MembershipPublic;
}

export interface CsrfTokenResponse {
  csrf_token: string;
}

export interface LogoutResponse {
  status: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  display_name: string;
  organization_name?: string | null;
  workspace_name?: string | null;
}

export interface LoginRequest {
  email: string;
  password: string;
}
