// Centralized frontend API client (MVP-02).
//
// Every request is credentialed (`credentials: "include"`) so the
// backend's HttpOnly session cookie is sent automatically — this module
// never reads, writes, or stores that cookie itself. The CSRF token is
// the one piece of session-bound state the frontend does hold, and it
// is kept in memory only (see `csrfToken` below), never in
// localStorage/sessionStorage, matching the backend's synchronizer-token
// design (apps/api/app/auth/csrf.py).

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL;

const CSRF_HEADER_NAME = "X-CSRF-Token";

export interface ApiErrorDetail {
  field: string;
  message: string;
}

// Mirrors the backend's single error envelope shape exactly (see
// apps/api/app/core/errors.py): `{"error": {"code","message","request_id","details"}}`.
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: ApiErrorDetail[];
  readonly requestId?: string;

  constructor(status: number, code: string, message: string, details?: ApiErrorDetail[], requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

// In-memory only — cleared on full page reload, which is correct: a
// fresh page load re-derives it from `GET /auth/csrf` the first time a
// mutating request needs it, the same way the session itself is re-
// checked from the HttpOnly cookie rather than assumed.
let csrfToken: string | null = null;

function isMutatingMethod(method: string): boolean {
  return method !== "GET" && method !== "HEAD";
}

async function readErrorEnvelope(response: Response): Promise<{ code: string; message: string; details?: ApiErrorDetail[]; requestId?: string }> {
  try {
    const data = (await response.json()) as { error?: { code?: string; message?: string; details?: ApiErrorDetail[]; request_id?: string } };
    if (data && typeof data === "object" && data.error) {
      return {
        code: data.error.code ?? "UNKNOWN_ERROR",
        message: data.error.message ?? "Request could not be processed.",
        details: data.error.details,
        requestId: data.error.request_id,
      };
    }
  } catch {
    // Response body was not JSON (or empty) — fall through to a generic error.
  }
  return { code: "UNKNOWN_ERROR", message: "Request could not be processed." };
}

async function fetchCsrfTokenInternal(): Promise<string> {
  if (!API_BASE_URL) {
    throw new ApiError(0, "CONFIG_ERROR", "NEXT_PUBLIC_API_URL is not configured.");
  }
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/auth/csrf`, { method: "GET", credentials: "include" });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Could not reach the server. Check your connection.");
  }
  if (!response.ok) {
    const envelope = await readErrorEnvelope(response);
    throw new ApiError(response.status, envelope.code, envelope.message, envelope.details, envelope.requestId);
  }
  const data = (await response.json()) as { csrf_token: string };
  csrfToken = data.csrf_token;
  return csrfToken;
}

/** Fetches and caches a fresh CSRF token. Exposed for explicit prefetching; `request()` also calls this lazily on first mutating call. */
export async function fetchCsrfToken(): Promise<string> {
  return fetchCsrfTokenInternal();
}

export function clearCsrfToken(): void {
  csrfToken = null;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  /** Set true for the two pre-session routes (register/login) that the backend deliberately does not require CSRF for. */
  skipCsrf?: boolean;
}

export async function request<T>(path: string, options: RequestOptions = {}, _isRetry = false): Promise<T> {
  if (!API_BASE_URL) {
    throw new ApiError(0, "CONFIG_ERROR", "NEXT_PUBLIC_API_URL is not configured.");
  }

  const method = options.method ?? "GET";
  const mutating = isMutatingMethod(method);
  const headers: Record<string, string> = {};
  let body: string | undefined;

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  if (mutating && !options.skipCsrf) {
    const token = csrfToken ?? (await fetchCsrfTokenInternal());
    headers[CSRF_HEADER_NAME] = token;
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { method, credentials: "include", headers, body });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Could not reach the server. Check your connection.");
  }

  if (response.ok) {
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  const envelope = await readErrorEnvelope(response);

  // CSRF token can legitimately go stale (a new session started elsewhere,
  // server restart, etc.) — this is the one error class explicit enough to
  // safely retry automatically, exactly once, with a freshly-fetched token.
  if (response.status === 403 && envelope.code === "CSRF_INVALID" && mutating && !options.skipCsrf && !_isRetry) {
    clearCsrfToken();
    return request<T>(path, options, true);
  }

  throw new ApiError(response.status, envelope.code, envelope.message, envelope.details, envelope.requestId);
}
