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
 * that would just 401. Instead we end the session and send the user to /login
 * via the server-side `GET /logout` route (a document navigation, not a
 * fetch). Ending the session (rather than a plain redirect) is required: a
 * refresh-failed session still has a valid cookie, so `auth()` keeps returning
 * it and the login page would bounce the user straight back. /logout expires
 * the cookies and deletes the server-side session entry, breaking the loop.
 *
 * An auth-401 *response* (stale/expired token — e.g. the first call after the
 * tab sat idle past the access token's 15 min lifespan) self-heals: one forced
 * session re-read (which rotates the token server-side) and one replay of the
 * request. Only a confirmed-dead session signs out; a second 401 on the fresh
 * token signs out too. See `authedFetch`.
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

// ── Log-scoped module-panel filter (processes/[logId] module views) ─────────
// A per-log ephemeral event filter (column filters + time range) set while
// viewing ONE log's module panels. Unlike the dashboard's ambient header above
// (a board-wide push onto every widget request), this is scoped to a single log
// and applies ONLY to that log's module data requests. The active log + its
// pre-encoded header live here so the merge below can read them lazily at fetch
// time; the provider (`components/processes/log-filter.tsx`) sets them on a
// filter commit and clears them on unmount. `header` is already base64-encoded
// by the SAME serializer the dashboard uses (`encodeFilterHeader`) — this module
// stays serializer-free and never imports from the component layer.
const LOG_EVENT_FILTER_HEADER = "X-FF-Event-Filter"; // mirrors widget-filter's EVENT_FILTER_HEADER
let _logScopedFilter: { logId: string; header: string } | null = null;

/** Set (or clear) the active log's module-panel filter. A null `header` — or a
 * null `logId` — clears it, restoring the byte-for-byte-unchanged no-filter
 * path in `applyAmbientHeaders`. Idempotent; safe to call from an effect and
 * its cleanup. */
export function setLogScopedFilter(logId: string | null, header: string | null): void {
  _logScopedFilter = logId && header ? { logId, header } : null;
}

// Module data requests are `/api/v1/modules/{id}/…?log_id={logId}` — the loader
// declares `log_id` as the route's query field, so it's always a query param.
// Return that log id ONLY for a module route (else null), so the filter can
// never ride onto a non-module request.
const MODULE_ROUTE_RE = /^\/api\/v1\/modules\//;
function moduleRequestLogId(path: string): string | null {
  if (!MODULE_ROUTE_RE.test(path)) return null;
  const q = path.indexOf("?");
  if (q === -1) return null;
  return new URLSearchParams(path.slice(q + 1)).get("log_id");
}

/** Merge ambient headers in, without clobbering anything the caller set. */
function applyAmbientHeaders(headers: Headers, path?: string): void {
  for (const [k, v] of _ambientHeaders) {
    if (!headers.has(k)) headers.set(k, v);
  }
  // Log-scoped module-panel filter — GATED & ADDITIVE. Attach the active log's
  // `X-FF-Event-Filter` ONLY when ALL hold: (a) a filter is set
  // (`_logScopedFilter` non-null), (b) this is a module data request for THAT
  // SAME log (`moduleRequestLogId(path) === lf.logId`), and (c) no event-filter
  // header is already present — a dashboard's ambient push above, or a
  // per-widget caller header — which must always win. When no log filter is set
  // (`_logScopedFilter === null`) this whole block is skipped, so the merge
  // above is byte-for-byte unchanged from the pre-feature behavior.
  const lf = _logScopedFilter;
  if (
    lf &&
    path &&
    !headers.has(LOG_EVENT_FILTER_HEADER) &&
    moduleRequestLogId(path) === lf.logId
  ) {
    headers.set(LOG_EVENT_FILTER_HEADER, lf.header);
  }
}

// Exact detail string the API's auth dependency returns for a missing,
// malformed, expired, or otherwise invalid bearer token
// (apps/api/src/mate/api/auth/dependencies.py, `_UNAUTH`). Other 401s exist —
// ai_models.py forwards upstream AI-provider 401s with an *object* detail —
// and those must never trigger a session refresh or a sign-out. If this
// string ever drifts, the fail-safe is the old behavior: throw, no retry.
// (`WWW-Authenticate` can't discriminate instead: the API is called
// cross-origin and CORS doesn't expose the header.)
const AUTH_401_DETAIL = "Missing or invalid bearer token";

function isAuthDetail(body: unknown): boolean {
  return (
    typeof body === "object" && body !== null && (body as { detail?: unknown }).detail === AUTH_401_DETAIL
  );
}

/** Is this response the API auth dependency rejecting the bearer token
 * (as opposed to an app-level 401, e.g. an AI provider rejecting a key)? */
async function isSessionAuth401(res: Response): Promise<boolean> {
  if (res.status !== 401) return false;
  try {
    // clone(): keep the body readable for the caller's error handling.
    return isAuthDetail(await res.clone().json());
  } catch {
    return false; // non-JSON 401 (proxy error page etc.) → not ours
  }
}

/** Human-readable message for an error response. FastAPI wraps errors as
 * `{"detail": ...}` — unwrap one level so toasts show `API 404: Event log
 * not found.` instead of a JSON blob. The raw parsed body stays on `.detail`. */
function apiErrorMessage(status: number, detail: unknown): string {
  const inner =
    detail && typeof detail === "object" && !Array.isArray(detail) && "detail" in detail
      ? (detail as { detail: unknown }).detail
      : detail;
  if (status === 401 && inner === AUTH_401_DETAIL) {
    return "Your session has expired. Please sign in again.";
  }
  if (typeof inner === "string" && inner) return `API ${status}: ${inner}`;
  if (inner == null || inner === "") return `API ${status}`;
  return `API ${status}: ${JSON.stringify(inner)}`;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(apiErrorMessage(status, detail));
    this.status = status;
    this.detail = detail;
  }
}

// ── Cached session ─────────────────────────────────────────────────────────
// `getSession()` always does a network roundtrip to `/api/auth/session`, and
// `attachAuth` awaited it before EVERY api() call – a page mounting six
// queries paid six extra serial roundtrips before the first real byte left
// the browser. Cache the session until shortly before its access token
// expires (`session.expiresAt`), clamped to [5s, 60s] and single-flighted so
// a burst of concurrent calls shares one fetch. An auth-401 from the backend
// forces a fresh re-read (rotating the token server-side) and one replay of
// the failed request — see `sessionAfter401` / `authedFetch`.
const SESSION_TTL_MIN_MS = 5_000;
const SESSION_TTL_MAX_MS = 60_000;
const TOKEN_EXPIRY_MARGIN_MS = 60_000;

// Sentinel: the `/api/auth/session` fetch itself failed (network error, or a
// browser content blocker refusing the request — seen in Safari with an ad/
// privacy blocker active). This is NOT the same as "logged out" (a reachable
// endpoint returning null). Collapsing the two used to force logoutToLogin() →
// /login → (cookie still valid server-side) → back → an infinite redirect loop,
// which `signOut()` couldn't even break because its own /api/auth/signout fetch
// was blocked by the same thing. Callers must treat this as "auth unknown".
const SESSION_FETCH_FAILED = Symbol("session-fetch-failed");
type SessionResult = Session | null | typeof SESSION_FETCH_FAILED;

let sessionCache: { session: SessionResult; validUntil: number } | null = null;
let sessionInflight: Promise<SessionResult> | null = null;

export function invalidateSessionCache(): void {
  sessionCache = null;
}

async function fetchSession(): Promise<SessionResult> {
  // Raw fetch instead of next-auth's getSession(): getSession() swallows every
  // non-OK response (a 500, an extension/content-blocker interception, a stale
  // service worker's error page) into `null` – indistinguishable from a real
  // "logged out". Callers would then sign out and redirect, and because the
  // cookie is still valid server-side, /login bounces straight back → the
  // Safari infinite login loop. Only an OK response whose JSON parses may
  // claim the user is signed out; everything else is "auth unknown".
  try {
    const res = await fetch("/api/auth/session", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) return SESSION_FETCH_FAILED;
    const data = (await res.json()) as unknown;
    // Auth.js returns `null` (or `{}`) with 200 when there is no session.
    if (!data || typeof data !== "object" || !("user" in data)) return null;
    return data as Session;
  } catch {
    return SESSION_FETCH_FAILED;
  }
}

async function cachedSession(): Promise<SessionResult> {
  if (sessionCache && Date.now() < sessionCache.validUntil) return sessionCache.session;
  if (sessionInflight) return sessionInflight;
  sessionInflight = fetchSession()
    .then((session) => {
      let ttl = SESSION_TTL_MAX_MS;
      if (session !== SESSION_FETCH_FAILED && session?.expiresAt) {
        // Never serve a token past (expiry − margin); near expiry this degrades
        // to 5s re-checks until the jwt callback rotates it server-side.
        ttl = Math.min(ttl, session.expiresAt * 1000 - TOKEN_EXPIRY_MARGIN_MS - Date.now());
      }
      // Missing/broken sessions, and a failed fetch, get the short TTL: don't
      // hammer during a burst, but recover quickly after sign-in / once the
      // endpoint is reachable again.
      if (session === SESSION_FETCH_FAILED || !session || session.error) ttl = SESSION_TTL_MIN_MS;
      sessionCache = { session, validUntil: Date.now() + Math.max(ttl, SESSION_TTL_MIN_MS) };
      return session;
    })
    .finally(() => {
      sessionInflight = null;
    });
  return sessionInflight;
}

// ── Auth-401 recovery ──────────────────────────────────────────────────────
let forced401Refresh: Promise<SessionResult> | null = null;

/** Fresh session read after an auth-401. `sentAuth` is the Authorization
 * header the failed request went out with (null if it went untokenized). If a
 * concurrent caller already refreshed and the cache holds a *different*
 * usable token, reuse it without another roundtrip; otherwise invalidate and
 * single-flight exactly one `/api/auth/session` fetch (whose jwt callback
 * rotates the token server-side). A burst of N concurrent 401s therefore
 * costs one session fetch; stragglers whose 401 lands after the refresh
 * replay straight from the cache. */
async function sessionAfter401(sentAuth: string | null): Promise<SessionResult> {
  if (sessionCache && Date.now() < sessionCache.validUntil) {
    const s = sessionCache.session;
    if (
      s !== SESSION_FETCH_FAILED &&
      s &&
      !s.error &&
      s.accessToken &&
      `Bearer ${s.accessToken}` !== sentAuth
    ) {
      return s;
    }
  }
  if (!forced401Refresh) {
    invalidateSessionCache();
    forced401Refresh = cachedSession().finally(() => {
      forced401Refresh = null;
    });
  }
  return forced401Refresh;
}

/** Current access token from the cached session (no per-call roundtrip).
 * `undefined` = no usable token (signed out, or refresh failed). */
export async function sessionAccessToken(): Promise<string | undefined> {
  if (typeof window === "undefined") return undefined;
  const session = await cachedSession();
  if (!session || session === SESSION_FETCH_FAILED || session.error === "RefreshAccessTokenError") {
    return undefined;
  }
  return session.accessToken;
}

async function browserToken(): Promise<string | undefined> {
  if (typeof window === "undefined") return undefined;
  return sessionAccessToken();
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
  // Top-level navigation through the server-side logout route – NOT signOut():
  // signOut() is a fetch to /api/auth/*, and whatever intercepts the session
  // fetch (content blocker, privacy extension, stale service worker – usually
  // the reason we're logging out at all) kills that fetch the same way. The
  // cookie then survives, /login sees a healthy session and bounces straight
  // back → infinite loop. A document navigation can't be intercepted by
  // fetch-layer blockers; /logout expires the cookies and deletes the
  // server-side session entry itself.
  window.location.assign(`/logout?callbackUrl=${encodeURIComponent(callbackUrl)}`);
}

async function attachAuth(headers: Headers): Promise<void> {
  if (typeof window === "undefined") return;
  const session = await cachedSession();
  if (session === SESSION_FETCH_FAILED) {
    // Couldn't reach /api/auth/session (network error, or a browser content
    // blocker). Fire the request untokenized rather than forcing a logout: the
    // old code collapsed this into "logged out" → logoutToLogin() → /login →
    // (cookie still valid server-side) → back → an infinite redirect loop
    // (reproduced in Safari with a content blocker active). A genuine 401 will
    // surface as an ApiError the caller handles; a real logout still returns a
    // null session below and redirects normally.
    return;
  }
  const token =
    session && session.error !== "RefreshAccessTokenError" ? session.accessToken : undefined;
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
    return;
  }
  // Confirmed no usable token (endpoint reachable, session gone or refresh
  // failed). Sign out and redirect rather than firing a guaranteed-401 request.
  await logoutToLogin();
}

/** Attach ambient headers + auth, fire the request, and self-heal auth-401s:
 * one forced session re-read (rotating the token server-side), one replay.
 * A second auth-401, or a confirmed-dead session, signs out via
 * `logoutToLogin()`. SESSION_FETCH_FAILED never signs out (content-blocker
 * loop protection — see `fetchSession`/`attachAuth`); app-level 401s (e.g. an
 * AI provider rejecting a stored key) pass through untouched. Replaying a
 * mutation is safe: the API's auth dependency runs before any route handler,
 * so an auth-401 guarantees the handler never executed. */
async function authedFetch(path: string, init: RequestInit, headers: Headers): Promise<Response> {
  applyAmbientHeaders(headers, path);
  await attachAuth(headers);
  const doFetch = () => fetch(`${apiBase()}${path}`, { ...init, headers });
  const res = await doFetch();
  if (res.status !== 401 || typeof window === "undefined") return res;
  if (signingOut) return res; // /logout navigation already underway
  if (init.body instanceof ReadableStream) return res; // one-shot body – cannot replay
  if (!(await isSessionAuth401(res))) return res; // app-level 401 – not ours
  const session = await sessionAfter401(headers.get("Authorization"));
  if (session === SESSION_FETCH_FAILED) return res; // auth unknown → surface the 401, never sign out
  const token =
    session && session.error !== "RefreshAccessTokenError" ? session.accessToken : undefined;
  if (!token) {
    await logoutToLogin(); // confirmed signed out / refresh dead
    return res;
  }
  // Replaying with the same token is fine too: a transient JWKS failure inside
  // the API raises this exact 401 and recovers on replay.
  headers.set("Authorization", `Bearer ${token}`);
  const retry = await doFetch();
  if (retry.status === 401 && (await isSessionAuth401(retry))) {
    invalidateSessionCache();
    await logoutToLogin(); // fresh token still rejected → give up
  }
  return retry;
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
  const res = await authedFetch(path, { ...init, cache: "no-store" }, headers);
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

/**
 * Multipart upload with progress reporting. `fetch` can't surface upload-byte
 * progress, so large files (e.g. a ~0.5 GB CV4CDD model archive) look frozen.
 * This uses `XMLHttpRequest` purely for its `upload.onprogress` events; auth +
 * error handling mirror `api()`. `onProgress` receives 0–100 for the bytes sent
 * to the server. Note the request stays pending after 100% while the server
 * processes the body (the model upload extracts the archive) – callers should
 * show an indeterminate "installing" state once progress reaches 100.
 */
export async function apiUpload<T = unknown>(
  path: string,
  file: File,
  opts: {
    fieldName?: string;
    /** Extra multipart form fields sent alongside the file. */
    fields?: Record<string, string>;
    onProgress?: (pct: number) => void;
  } = {},
): Promise<T> {
  const token = await browserToken();
  if (!token) {
    // No usable bearer token: sign out + redirect rather than a guaranteed 401.
    await logoutToLogin();
    throw new ApiError(401, "Not authenticated");
  }
  const attempt = (bearer: string) =>
    new Promise<T>((resolve, reject) => {
      const body = new FormData();
      body.append(opts.fieldName ?? "file", file);
      for (const [key, value] of Object.entries(opts.fields ?? {})) {
        body.append(key, value);
      }
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${apiBase()}${path}`);
      xhr.setRequestHeader("Authorization", `Bearer ${bearer}`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && opts.onProgress) {
          opts.onProgress((e.loaded / e.total) * 100);
        }
      };
      xhr.onload = () => {
        let detail: unknown = xhr.responseText;
        try {
          detail = JSON.parse(xhr.responseText);
        } catch {
          /* keep as text */
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve((xhr.status === 204 ? undefined : detail) as T);
        } else {
          reject(new ApiError(xhr.status, detail));
        }
      };
      xhr.onerror = () => reject(new ApiError(0, "Network error during upload"));
      xhr.send(body);
    });
  try {
    return await attempt(token);
  } catch (err) {
    // Auth-401 → same self-heal as authedFetch: one forced session refresh,
    // one re-upload (progress restarts from 0 – acceptable for the rare
    // mid-upload token expiry). Everything else rethrows untouched; the
    // preflight ApiError(401, "Not authenticated") above has a string detail,
    // so it can never re-enter this branch.
    if (!(err instanceof ApiError) || err.status !== 401 || !isAuthDetail(err.detail)) throw err;
    const session = await sessionAfter401(`Bearer ${token}`);
    if (session === SESSION_FETCH_FAILED) throw err; // auth unknown – never sign out
    const fresh =
      session && session.error !== "RefreshAccessTokenError" ? session.accessToken : undefined;
    if (!fresh) {
      await logoutToLogin();
      throw err;
    }
    return await attempt(fresh);
  }
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
  return authedFetch(path, init, headers);
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
