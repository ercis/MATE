"use client";

import { Suspense, useEffect, useRef } from "react";
import { usePathname, useSearchParams } from "next/navigation";

import { api } from "@/lib/api";
import { useAnalytics } from "@/lib/stores/analytics";
import {
  discardQueue,
  enqueueEvent,
  flush,
  flushOnUnload,
  startFlushTimer,
  stopFlushTimer,
} from "@/lib/analytics/client";
import { shouldRespectPrivacySignal } from "@/lib/analytics/dnt";
import { EV } from "@/lib/analytics/events";

interface ServerConfig {
  enabled: boolean;
  retention_days: number | null;
  capture_clicks: boolean;
  capture_perf: boolean;
  capture_errors: boolean;
  opted_in_at: string | null;
  anon_user_id_seed: string;
  onboarding_mode: "force" | "on" | "off";
}

/**
 * Mounts the auto-tracking listeners (page views, clicks, errors,
 * web-vitals) and keeps the analytics store synchronised with the server
 * config row. Always renders its children – the gates are entirely inside
 * the effects.
 */
export function AnalyticsProvider({ children }: { children: React.ReactNode }) {
  // Sync server config into the store once on mount. The server seed wins
  // – local opt-in is honoured only if the seed matches.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const cfg = await api<ServerConfig>("/api/v1/usage/config");
        if (cancelled) return;
        const store = useAnalytics.getState();
        if (store.anonUserId !== cfg.anon_user_id_seed) {
          store.setAnonUserId(cfg.anon_user_id_seed);
        }
        store.setCaptureFlags({
          captureClicks: cfg.capture_clicks,
          capturePerf: cfg.capture_perf,
          captureErrors: cfg.capture_errors,
        });
        // Under `force` the admin mandates tracking for every user, so we
        // honour the server's enabled=true even past a browser DNT/GPC signal.
        if (cfg.onboarding_mode !== "force" && shouldRespectPrivacySignal()) {
          store.setEnabled(false);
        } else {
          store.setEnabled(cfg.enabled);
        }
      } catch {
        // Backend unreachable – leave the store as-is. The next flush will
        // simply drop on the floor.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Flush timer + unload handler are global; install once.
  useEffect(() => {
    startFlushTimer();
    const onHide = () => flushOnUnload();
    const onPageHide = () => flushOnUnload();
    window.addEventListener("beforeunload", onHide);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.removeEventListener("beforeunload", onHide);
      window.removeEventListener("pagehide", onPageHide);
      stopFlushTimer();
    };
  }, []);

  return (
    <Suspense fallback={null}>
      <AutoTrackers />
      {children}
    </Suspense>
  );
}

/**
 * Split out so `useSearchParams()` can sit inside the Suspense boundary as
 * required by the Next.js App Router.
 */
function AutoTrackers() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const lastPathRef = useRef<string | null>(null);

  // Page views – pathname only; we intentionally do NOT pass query params
  // since they often carry identifiers we don't want to capture.
  useEffect(() => {
    if (!pathname) return;
    if (lastPathRef.current === pathname) return;
    // The path we're navigating away from – lets dashboards reconstruct
    // "clicked on X → landed on Y" from the from_path → path transition.
    const fromPath = lastPathRef.current;
    lastPathRef.current = pathname;
    enqueueEvent({
      event_type: "page",
      event_name: EV.PAGE_VIEW,
      path: pathname,
      referrer: typeof document !== "undefined" ? document.referrer || null : null,
      properties: {
        from_path: fromPath,
        has_query: (searchParams?.toString().length ?? 0) > 0,
      },
    });
  }, [pathname, searchParams]);

  // Delegated click capture – every click on the document is recorded, not
  // just interactive elements. We attach the nearest button/link if one
  // exists in the ancestor chain so dashboards can still group by action,
  // but raw target metadata is always present. Left-click (`click`),
  // middle-click (`auxclick`), and right-click (`contextmenu`) are all
  // captured; the `kind` property records which. Skipped only when the target
  // (or an ancestor) is marked `data-no-track`.
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!useAnalytics.getState().captureClicks) return;
      const target = e.target;
      if (!(target instanceof Element)) return;
      if (target.closest("[data-no-track]")) return;
      // `contextmenu` fires for keyboard-menu / touch-hold too – coerce the
      // discrete event types to a stable label for grouping.
      const kind =
        e.type === "auxclick"
          ? "auxclick"
          : e.type === "contextmenu"
            ? "contextmenu"
            : "click";

      const targetEl = target as HTMLElement;
      const action = targetEl.closest<HTMLElement>(
        "button, a, [role='button'], [data-track]",
      );
      const trackName = action?.getAttribute("data-track-name") ?? null;
      const href = action?.getAttribute("href") ?? null;
      const hrefHost = (() => {
        if (!href) return null;
        try {
          return new URL(href, window.location.origin).host || null;
        } catch {
          return null;
        }
      })();
      // For in-app links, capture the destination pathname (no query) so a
      // click records where it was about to take the user.
      const hrefPath = (() => {
        if (!href) return null;
        try {
          const u = new URL(href, window.location.origin);
          return u.host === window.location.host ? u.pathname : null;
        } catch {
          return null;
        }
      })();
      const text = (action?.textContent ?? targetEl.textContent ?? "")
        .trim()
        .slice(0, 80);
      const classes = (targetEl.className && typeof targetEl.className === "string"
        ? targetEl.className
        : ""
      )
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 6)
        .join(" ")
        .slice(0, 120);

      enqueueEvent({
        event_type: "click",
        event_name: trackName || EV.CLICK,
        path: window.location.pathname,
        properties: {
          kind,
          target_tag: targetEl.tagName.toLowerCase(),
          target_id: targetEl.id || null,
          target_classes: classes || null,
          action_tag: action ? action.tagName.toLowerCase() : null,
          action_id: action?.id || null,
          text: text || null,
          href_host: hrefHost,
          href_path: hrefPath,
          button: e.button,
          modifiers:
            (e.shiftKey ? "s" : "") +
            (e.ctrlKey ? "c" : "") +
            (e.metaKey ? "m" : "") +
            (e.altKey ? "a" : "") || null,
        },
      });
    };
    // Capture-phase so we see the click even if a child stops propagation.
    // `auxclick` = middle button, `contextmenu` = right button / menu key.
    document.addEventListener("click", handler, { capture: true });
    document.addEventListener("auxclick", handler, { capture: true });
    document.addEventListener("contextmenu", handler, { capture: true });
    return () => {
      document.removeEventListener("click", handler, { capture: true });
      document.removeEventListener("auxclick", handler, { capture: true });
      document.removeEventListener("contextmenu", handler, { capture: true });
    };
  }, []);

  // Global error capture
  useEffect(() => {
    const onErr = (event: ErrorEvent) => {
      if (!useAnalytics.getState().captureErrors) return;
      enqueueEvent({
        event_type: "error",
        event_name: EV.CLIENT_ERROR,
        path: window.location.pathname,
        properties: {
          message: event.message?.slice(0, 240) ?? null,
          source: event.filename?.slice(0, 240) ?? null,
          lineno: event.lineno ?? null,
        },
      });
    };
    const onRej = (event: PromiseRejectionEvent) => {
      if (!useAnalytics.getState().captureErrors) return;
      const reason = event.reason;
      enqueueEvent({
        event_type: "error",
        event_name: EV.CLIENT_ERROR,
        path: window.location.pathname,
        properties: {
          message:
            (typeof reason === "string"
              ? reason
              : (reason as { message?: string })?.message
            )?.slice(0, 240) ?? null,
          kind: "unhandledrejection",
        },
      });
    };
    window.addEventListener("error", onErr);
    window.addEventListener("unhandledrejection", onRej);
    return () => {
      window.removeEventListener("error", onErr);
      window.removeEventListener("unhandledrejection", onRej);
    };
  }, []);

  // Core Web Vitals (loaded lazily; absent dependency is non-fatal).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const wv = await import("web-vitals");
        if (cancelled) return;
        const report = (name: string) =>
          (metric: { value: number; rating?: string; id: string }) => {
            if (!useAnalytics.getState().capturePerf) return;
            enqueueEvent({
              event_type: "perf",
              event_name: EV.WEB_VITAL,
              path: window.location.pathname,
              properties: {
                metric: name,
                value: metric.value,
                rating: metric.rating ?? null,
                id: metric.id,
              },
            });
          };
        wv.onCLS(report("CLS"));
        wv.onLCP(report("LCP"));
        wv.onINP(report("INP"));
        wv.onFCP(report("FCP"));
        wv.onTTFB(report("TTFB"));
      } catch {
        /* web-vitals not installed; perf tracking off */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Visibility-driven flush – when the tab is hidden, push any pending
  // events so we don't lose them if the browser kills the tab.
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "hidden") {
        void flush();
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  // When the user toggles off, drop the queue immediately so nothing
  // already-collected sneaks out on the next flush tick.
  useEffect(() => {
    const unsub = useAnalytics.subscribe((s, prev) => {
      if (prev.enabled && !s.enabled) discardQueue();
    });
    return () => unsub();
  }, []);

  return null;
}
