/**
 * Typed-fetch wrapper that auto-attaches the Keycloak access token.
 *
 * - The browser hits the FastAPI backend directly via `NEXT_PUBLIC_API_URL`
 *   (CORS is configured on the API side). It reads the token through Auth.js's
 *   `getSession()` (works in client components and event handlers).
 * - Server-side callers (RSCs, route handlers) should use `apiServer()` from
 *   `./api-server` so the token comes from the cookie-backed `auth()` helper.
 *
 * If the session is missing or `session.error === "RefreshAccessTokenError"`,
 * there is no usable bearer token, so we don't fire an unauthenticated request
 * that would just 401. Instead we sign the user out and send them to `/login`.
 * Signing out (rather than a plain redirect) is required: a refresh-failed
 * session still has a valid JWT cookie, so `auth()` keeps returning it and the
 * login page would bounce the user straight back. `signOut()` clears that
 * cookie and ends the Keycloak SSO session, breaking the loop.
 */

import type { Session } from "next-auth";

const SERVER_BASE = process.env.INTERNAL_API_URL ?? "http://localhost:8000";
const PUBLIC_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function apiBase() {
  if (typeof window === "undefined") return SERVER_BASE;
  return PUBLIC_BASE;
}

// ── Ambient request headers ────────────────────────────────────────────────
// A browser-only registry of headers attached to every `api()` / `rawFetch()`
// call. The dashboard uses it to push its ephemeral `X-FF-Event-Filter` onto
// all module-widget requests without touching any widget's fetch code (the
// backend only reads the header in its module-route dispatch, so it's a no-op
// elsewhere). Set/clear it from a provider's mount/unmount lifecycle.
const _ambientHeaders = new Map<string, string>();

export function setAmbientHeaders(headers: Record<string, string | null | undefined>): void {
  for (const [k, v] of Object.entries(headers)) {
    if (v == null) _ambientHeaders.delete(k);
    else _ambientHeaders.set(k, v);
  }
}

export function clearAmbientHeaders(...keys: string[]): void {
  if (keys.length === 0) _ambientHeaders.clear();
  else for (const k of keys) _ambientHeaders.delete(k);
}

/** Merge ambient headers in, without clobbering anything the caller set. */
function applyAmbientHeaders(headers: Headers): void {
  for (const [k, v] of _ambientHeaders) {
    if (!headers.has(k)) headers.set(k, v);
  }
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(`API ${status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
    this.status = status;
    this.detail = detail;
  }
}

async function browserToken(): Promise<string | undefined> {
  if (typeof window === "undefined") return undefined;
  try {
    const { getSession } = await import("next-auth/react");
    const session = (await getSession()) as Session | null;
    if (session?.error === "RefreshAccessTokenError") return undefined;
    return session?.accessToken;
  } catch {
    return undefined;
  }
}

// Module-level guard so a burst of concurrent calls that all hit a missing
// token triggers exactly one sign-out, not one per in-flight request.
let signingOut = false;

/** No usable token in the browser → end the session and go to /login. Exported
 * so the session guard (`components/session-guard.tsx`) reuses the same
 * single-flight sign-out + on-/login no-op guard instead of racing its own. */
export async function logoutToLogin(): Promise<void> {
  if (typeof window === "undefined") return;
  if (signingOut) return;
  // Already on the login surface – nothing to do (and avoids a redirect loop).
  if (window.location.pathname.startsWith("/login")) return;
  signingOut = true;
  const callbackUrl = `${window.location.pathname}${window.location.search}`;
  const loginUrl = `/login?callbackUrl=${encodeURIComponent(callbackUrl)}`;
  try {
    const { signOut } = await import("next-auth/react");
    await signOut({ redirectTo: loginUrl });
  } catch {
    // Fallback: hard-navigate to login if sign-out couldn't run.
    window.location.assign(loginUrl);
  }
}

async function attachAuth(headers: Headers): Promise<void> {
  const token = await browserToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
    return;
  }
  // Browser request with no bearer token: the session is gone or its refresh
  // failed. Sign out and redirect rather than firing a guaranteed-401 request.
  if (typeof window !== "undefined") {
    await logoutToLogin();
  }
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    init.body = JSON.stringify(init.json);
  }
  applyAmbientHeaders(headers);
  await attachAuth(headers);
  const res = await fetch(`${apiBase()}${path}`, { ...init, headers, cache: "no-store" });
  if (!res.ok) {
    let detail: unknown = await res.text();
    try {
      detail = JSON.parse(detail as string);
    } catch {
      /* keep as text */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

/** Raw fetch that returns the Response without JSON-parsing – use for SSE / streaming endpoints. */
export async function rawFetch(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    init.body = JSON.stringify(init.json);
  }
  applyAmbientHeaders(headers);
  await attachAuth(headers);
  return fetch(`${apiBase()}${path}`, { ...init, headers });
}

/** Build an absolute URL pointing at the backend. Use for `<img src>`, `<a href>`,
 * and any other browser-side reference that bypasses the `api()` helper.
 *
 * Static module assets are served from this URL – the API doesn't require a
 * token for `/api/v1/modules/{id}/assets/*` (they're public bundles).
 */
export function apiUrl(path: string): string {
  return `${apiBase()}${path}`;
}
