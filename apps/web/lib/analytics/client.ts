"use client";

import { apiUrl } from "@/lib/api";
import { useAnalytics } from "@/lib/stores/analytics";
import { deriveUaClass } from "@/lib/analytics/dnt";

import type { EventName } from "@/lib/analytics/events";

/**
 * Tracking client – a thin queue that batches events and POSTs to
 * `/api/v1/usage/sync`. The server is the source of truth for
 * whether ingestion is on; this client gates as a UX shortcut.
 */

interface QueuedEvent {
  event_type: "page" | "click" | "custom" | "error" | "perf" | "form";
  event_name: string;
  occurred_at: string;
  path?: string | null;
  referrer?: string | null;
  properties?: Record<string, unknown> | null;
}

const FLUSH_INTERVAL_MS = 5_000;
const MAX_QUEUE_BEFORE_FLUSH = 50;
const SESSION_IDLE_MS = 30 * 60 * 1000;
// Path deliberately neutral so ad-blocker filter lists (EasyPrivacy etc.)
// don't drop our requests with `net::ERR_BLOCKED_BY_CLIENT`. Matches the
// backend `/usage` router.
const INGEST_PATH = "/api/v1/usage/sync";

let queue: QueuedEvent[] = [];
let flushTimer: ReturnType<typeof setInterval> | null = null;
let flushing: Promise<void> | null = null;
// Regression guard: ingest silently 401'd for a long time because batches went
// out without a bearer token. Warn once if the server ever rejects a batch so
// a broken auth path is visible instead of just dropping every event.
let warnedIngestFailure = false;

// The ingest endpoint requires a bearer token (same as every other API call).
// We cache the most recent access token so the synchronous unload path can
// authenticate too – `navigator.sendBeacon` can't set an Authorization header.
let cachedToken: string | null = null;

async function accessToken(): Promise<string | null> {
  try {
    const { getSession } = await import("next-auth/react");
    const session = (await getSession()) as
      | { accessToken?: string; error?: string }
      | null;
    // Don't reuse a token from a session that failed to refresh.
    cachedToken = session?.error ? null : session?.accessToken ?? null;
  } catch {
    // Keep the last known token on a transient failure.
  }
  return cachedToken;
}

function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  // Sufficient fallback for non-cryptographic identifiers.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function ensureSession(): { sessionId: string; started: number } {
  const s = useAnalytics.getState();
  const now = Date.now();
  const stale = s.lastActivityAt && now - s.lastActivityAt > SESSION_IDLE_MS;
  if (!s.sessionId || stale) {
    const id = uuid();
    useAnalytics.getState().beginSession(id);
    return { sessionId: id, started: now };
  }
  useAnalytics.getState().touchSession();
  return { sessionId: s.sessionId, started: s.sessionStartedAt ?? now };
}

function sessionMeta(sessionId: string, started: number) {
  const s = useAnalytics.getState();
  return {
    id: sessionId,
    anon_user_id: s.anonUserId ?? "",
    started_at: new Date(started).toISOString(),
    entry_path: typeof window !== "undefined" ? window.location.pathname : null,
    viewport_w: typeof window !== "undefined" ? window.innerWidth : null,
    viewport_h: typeof window !== "undefined" ? window.innerHeight : null,
    ua_class: deriveUaClass(),
    locale:
      typeof navigator !== "undefined" ? navigator.language || null : null,
    tz:
      typeof Intl !== "undefined"
        ? Intl.DateTimeFormat().resolvedOptions().timeZone || null
        : null,
  };
}

export function enqueueEvent(event: Omit<QueuedEvent, "occurred_at">): void {
  const s = useAnalytics.getState();
  if (!s.enabled || !s.anonUserId) return;
  queue.push({ ...event, occurred_at: new Date().toISOString() });
  if (queue.length >= MAX_QUEUE_BEFORE_FLUSH) {
    void flush();
  }
}

export function trackCustom(
  name: EventName | string,
  properties?: Record<string, unknown>,
): void {
  enqueueEvent({
    event_type: "custom",
    event_name: name,
    path:
      typeof window !== "undefined" ? window.location.pathname : null,
    properties: properties ?? null,
  });
}

export async function flush(): Promise<void> {
  if (flushing) return flushing;
  if (queue.length === 0) return;
  const s = useAnalytics.getState();
  if (!s.enabled || !s.anonUserId) {
    queue = [];
    return;
  }
  const batch = queue;
  queue = [];
  const { sessionId, started } = ensureSession();
  const payload = {
    session: sessionMeta(sessionId, started),
    events: batch,
  };
  flushing = (async () => {
    try {
      const token = await accessToken();
      if (!token) {
        // Session token not ready yet (e.g. just after load). Re-queue the
        // batch and try again next tick rather than POST unauthenticated –
        // the endpoint requires a bearer token and would 401 the events away.
        queue = batch.concat(queue);
        return;
      }
      const res = await fetch(apiUrl(INGEST_PATH), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
        keepalive: true,
      });
      // 204 means the server has disabled ingest. Sync the client so we
      // stop wasting cycles until the user re-enables.
      if (res.status === 204) {
        useAnalytics.getState().setEnabled(false);
      } else if (!res.ok && !warnedIngestFailure) {
        // 202 = accepted, 204 = disabled (handled above); anything else is a
        // real rejection (401 = missing/expired token, 5xx = server error).
        warnedIngestFailure = true;
        console.warn(
          `[analytics] event ingest rejected with HTTP ${res.status}; events are ` +
            "being dropped. /api/v1/usage/sync likely received no valid bearer token.",
        );
      }
    } catch {
      // Silent: analytics must never crash the host app. Dropping a batch
      // is preferable to retry storms during a backend outage.
    } finally {
      flushing = null;
    }
  })();
  return flushing;
}

/**
 * Last-ditch flush on unload. Must be synchronous, so we can't fetch a fresh
 * token here – we reuse the one cached by the periodic `flush()` (which runs
 * every few seconds and on tab-hide, so it's normally current). An
 * authenticated `keepalive` fetch survives the unload and, unlike
 * `sendBeacon`, can carry the bearer token the ingest endpoint requires;
 * `sendBeacon` is only a last resort (it will 401 without a token).
 */
export function flushOnUnload(): void {
  if (queue.length === 0) return;
  const s = useAnalytics.getState();
  if (!s.enabled || !s.anonUserId || typeof navigator === "undefined") {
    queue = [];
    return;
  }
  const { sessionId, started } = ensureSession();
  const payload = {
    session: sessionMeta(sessionId, started),
    events: queue,
  };
  queue = [];
  const body = JSON.stringify(payload);
  if (cachedToken) {
    try {
      void fetch(apiUrl(INGEST_PATH), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${cachedToken}`,
        },
        body,
        keepalive: true,
      });
      return;
    } catch {
      /* fall through to sendBeacon */
    }
  }
  try {
    const blob = new Blob([body], { type: "application/json" });
    navigator.sendBeacon(apiUrl(INGEST_PATH), blob);
  } catch {
    /* swallow */
  }
}

export function startFlushTimer(): void {
  if (flushTimer != null) return;
  flushTimer = setInterval(() => {
    void flush();
  }, FLUSH_INTERVAL_MS);
}

export function stopFlushTimer(): void {
  if (flushTimer != null) {
    clearInterval(flushTimer);
    flushTimer = null;
  }
}

/** Drop any queued events without sending. Used after opt-out. */
export function discardQueue(): void {
  queue = [];
}
