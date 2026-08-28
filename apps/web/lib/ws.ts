"use client";

/**
 * Live-update stream client (job lifecycle + log-import toasts/drawer).
 *
 * Transport is **Server-Sent Events over `fetch`**, despite the historical
 * `ws` filename – which is kept because `@/lib/ws` is a published module-SDK
 * import alias (see `runtime-externals.json`). Production sits behind a proxy
 * chain (uni edge proxy → Caddy → api) that carries HTTP streaming
 * transparently but drops WS upgrades – the handshake reaches the API as a
 * plain GET and 404s – so the old WebSocket bus silently died in prod. SSE
 * rides the same path Mate AI streaming already uses (`/api/v1/ai/chat`).
 *
 * `subscribeBus` opens `GET /api/v1/events?topic=…` (one per session) for
 * topic-filtered fan-out. `subscribeJob` opens `GET /api/v1/jobs/{id}/stream`
 * for high-frequency progress on a focused job.
 *
 * Auth is the standard `Authorization: Bearer` header (via `rawFetch`), so –
 * unlike the old WS client – the token no longer rides in the URL where it
 * leaked into the API access logs. HTTP `401` means "auth failed – sign back
 * in" rather than reconnecting in a loop. Both reconnect with exponential
 * backoff (capped at 8s) so a transient blip never breaks the pipeline.
 */

import { logoutToLogin, rawFetch } from "@/lib/api";
import type { BusEnvelope } from "@/lib/api-types";

type Listener<T> = (env: BusEnvelope<T>) => void;

interface Subscription {
  close: () => void;
}

const BACKOFF_MS = [250, 500, 1000, 2000, 4000, 8000];

/** Pull the `data:` payload out of one SSE frame; `null` for comment-only
 * frames (`: ping` keep-alives) and for events that carry no data lines. */
function frameData(frame: string): string | null {
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue; // comment / keep-alive
    if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  return data.length ? data.join("\n") : null;
}

/**
 * Open an SSE stream and dispatch each parsed envelope. Reconnects with backoff
 * unless `closed`, a `401` is seen (→ sign in), or the status is in `stopOn`.
 */
function subscribeSse<T>(
  path: string,
  onMessage: Listener<T>,
  opts: { stopOn?: number[] } = {},
): Subscription {
  let attempt = 0;
  let closed = false;
  let controller: AbortController | null = null;

  const reconnect = () => {
    if (closed) return;
    const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
    attempt += 1;
    setTimeout(() => void open(), delay);
  };

  const open = async () => {
    if (closed) return;
    controller = new AbortController();

    let res: Response;
    try {
      res = await rawFetch(path, {
        headers: { Accept: "text/event-stream" },
        cache: "no-store",
        signal: controller.signal,
      });
    } catch {
      reconnect(); // network error before headers – retry
      return;
    }

    if (res.status === 401) {
      // Auth failed – end the session and bounce to /login rather than
      // reconnecting in a loop. Shares lib/api's logoutToLogin (single-flight,
      // no-op on /login), which navigates through the server-side /logout
      // route: a fetch-based signOut() dies under the same interception
      // (content blocker / stale service worker) that tends to cause this
      // state, leaving the cookie alive and /login bouncing straight back.
      await logoutToLogin();
      return;
    }
    if (opts.stopOn?.includes(res.status)) return;
    if (!res.ok || !res.body) {
      reconnect();
      return;
    }
    attempt = 0; // connected – reset backoff

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let sep: number;
        // SSE frames are separated by a blank line.
        while ((sep = buf.indexOf("\n\n")) !== -1) {
          const data = frameData(buf.slice(0, sep));
          buf = buf.slice(sep + 2);
          if (data === null) continue;
          try {
            onMessage(JSON.parse(data) as BusEnvelope<T>);
          } catch (err) {
            console.error("sse.parse_error", err);
          }
        }
      }
    } catch {
      // Aborted (close) or a mid-stream blip – fall through to reconnect.
    }
    reconnect();
  };

  void open();

  return {
    close: () => {
      closed = true;
      controller?.abort();
    },
  };
}

export function subscribeBus<T = Record<string, unknown>>(
  topics: string[],
  onMessage: Listener<T>,
): Subscription {
  const qs = topics.map((t) => `topic=${encodeURIComponent(t)}`).join("&");
  return subscribeSse(`/api/v1/events${qs ? `?${qs}` : ""}`, onMessage);
}

export function subscribeJob<T = Record<string, unknown>>(
  jobId: string,
  onMessage: Listener<T>,
): Subscription {
  // 404 = job not found; don't retry.
  return subscribeSse(`/api/v1/jobs/${encodeURIComponent(jobId)}/stream`, onMessage, {
    stopOn: [404],
  });
}
