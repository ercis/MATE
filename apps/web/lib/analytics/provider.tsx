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
import {
  activityName,
  cssPath,
  elementState,
  elementTarget,
  isSecretField,
  uiGroups,
} from "@/lib/analytics/dom";
import { EV } from "@/lib/analytics/events";

interface ServerConfig {
  enabled: boolean;
  retention_days: number | null;
  capture_clicks: boolean;
  capture_perf: boolean;
  capture_errors: boolean;
  capture_inputs: boolean;
  capture_keyboard: boolean;
  capture_pointer: boolean;
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
          captureInputs: cfg.capture_inputs,
          captureKeyboard: cfg.capture_keyboard,
          capturePointer: cfg.capture_pointer,
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
            : e.type === "dblclick"
              ? "dblclick"
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

      // UI-hierarchy context (Abb & Rehse model): the action element (or raw
      // target) as ui_element with selector + state, plus the ancestor
      // ui_group chain. The activity name concatenates action type + target.
      const uiEl = action ?? targetEl;
      const targetInfo = elementTarget(uiEl);
      const captureInputs = useAnalytics.getState().captureInputs;

      enqueueEvent({
        event_type: "click",
        event_name: trackName || EV.CLICK,
        path: window.location.pathname,
        properties: {
          kind,
          activity: activityName(kind, targetInfo),
          selector: cssPath(uiEl),
          target: targetInfo,
          state: elementState(uiEl, captureInputs),
          ui_groups: uiGroups(uiEl),
          x: e.clientX,
          y: e.clientY,
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
    document.addEventListener("dblclick", handler, { capture: true });
    return () => {
      document.removeEventListener("click", handler, { capture: true });
      document.removeEventListener("auxclick", handler, { capture: true });
      document.removeEventListener("contextmenu", handler, { capture: true });
      document.removeEventListener("dblclick", handler, { capture: true });
    };
  }, []);

  // Committed input values (paper: input value on the activity). Captured on
  // `change` – i.e. on commit, never per keystroke – with hard redaction for
  // password/credential fields regardless of settings (matches the paper's
  // working example, which never reads the login mask).
  useEffect(() => {
    const handler = (e: Event) => {
      if (!useAnalytics.getState().captureInputs) return;
      const el = e.target;
      if (!(el instanceof HTMLElement)) return;
      if (el.closest("[data-no-track]")) return;
      if (
        !(
          el instanceof HTMLInputElement ||
          el instanceof HTMLTextAreaElement ||
          el instanceof HTMLSelectElement
        )
      ) {
        return;
      }
      const target = elementTarget(el);
      const secret = isSecretField(el);
      const state = elementState(el, true);
      let inputValue: string | null = null;
      if (!secret) {
        if (el instanceof HTMLSelectElement) {
          inputValue = el.selectedOptions[0]?.textContent?.trim().slice(0, 256) ?? null;
        } else if (el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")) {
          inputValue = el.checked ? "checked" : "unchecked";
        } else {
          inputValue = el.value.slice(0, 256);
        }
      }
      enqueueEvent({
        event_type: "input",
        event_name: EV.INPUT_CHANGE,
        path: window.location.pathname,
        properties: {
          kind: "input",
          activity: activityName("enter value", target),
          selector: cssPath(el),
          target,
          state,
          ui_groups: uiGroups(el),
          input_value: inputValue,
          redacted: secret || null,
        },
      });
    };
    document.addEventListener("change", handler, { capture: true });
    return () => document.removeEventListener("change", handler, { capture: true });
  }, []);

  // Keyboard: modifier combos + special keys only – never plain typing (the
  // committed text arrives via the change listener above).
  useEffect(() => {
    const SPECIAL = new Set([
      "Escape",
      "Enter",
      "Tab",
      "Delete",
      "F1", "F2", "F3", "F4", "F5", "F6",
      "F7", "F8", "F9", "F10", "F11", "F12",
    ]);
    const handler = (e: KeyboardEvent) => {
      if (!useAnalytics.getState().captureKeyboard) return;
      if (e.repeat) return;
      const hasModifier = e.ctrlKey || e.metaKey || e.altKey;
      if (!hasModifier && !SPECIAL.has(e.key)) return;
      // Modifier keydowns themselves are noise.
      if (["Control", "Meta", "Alt", "Shift"].includes(e.key)) return;
      const el = document.activeElement;
      const targetEl = el instanceof HTMLElement ? el : null;
      if (targetEl?.closest("[data-no-track]")) return;
      const combo =
        (e.ctrlKey ? "Ctrl+" : "") +
        (e.metaKey ? "Meta+" : "") +
        (e.altKey ? "Alt+" : "") +
        (e.shiftKey ? "Shift+" : "") +
        (e.key.length === 1 ? e.key.toUpperCase() : e.key);
      const target = targetEl ? elementTarget(targetEl) : null;
      enqueueEvent({
        event_type: "key",
        event_name: EV.HOTKEY,
        path: window.location.pathname,
        properties: {
          kind: "key",
          activity: `press ${combo}`,
          combo,
          selector: targetEl ? cssPath(targetEl) : null,
          target,
          ui_groups: targetEl ? uiGroups(targetEl) : [],
        },
      });
    };
    document.addEventListener("keydown", handler, { capture: true });
    return () => document.removeEventListener("keydown", handler, { capture: true });
  }, []);

  // Pointer traces – sampled mouse movement (≥50ms and ≥8px between points),
  // flushed as one polyline segment event every ≤2s / 40 points / 1s idle.
  // An extension over the paper's discrete-action model; volume-bounded and
  // excluded from unload flushes + session event counts.
  useEffect(() => {
    const MIN_DT_MS = 50;
    const MIN_DIST_PX = 8;
    const SEGMENT_MS = 2000;
    const IDLE_MS = 1000;
    const MAX_POINTS = 40;
    let points: Array<[number, number, number]> = [];
    let segmentStart = 0;
    let lastX = -1;
    let lastY = -1;
    let lastT = 0;
    let idleTimer: ReturnType<typeof setTimeout> | null = null;

    const emit = () => {
      if (idleTimer != null) {
        clearTimeout(idleTimer);
        idleTimer = null;
      }
      if (points.length < 2) {
        points = [];
        return;
      }
      enqueueEvent({
        event_type: "pointer",
        event_name: EV.POINTER_TRACE,
        path: window.location.pathname,
        properties: {
          kind: "pointer_trace",
          activity: "move pointer",
          points,
          duration_ms: points[points.length - 1][2],
        },
      });
      points = [];
    };

    const handler = (e: PointerEvent) => {
      if (!useAnalytics.getState().capturePointer) return;
      const now = performance.now();
      if (points.length === 0) {
        segmentStart = now;
        points.push([Math.round(e.clientX), Math.round(e.clientY), 0]);
      } else {
        const dt = now - lastT;
        const dist = Math.hypot(e.clientX - lastX, e.clientY - lastY);
        if (dt < MIN_DT_MS || dist < MIN_DIST_PX) return;
        points.push([
          Math.round(e.clientX),
          Math.round(e.clientY),
          Math.round(now - segmentStart),
        ]);
      }
      lastX = e.clientX;
      lastY = e.clientY;
      lastT = now;
      if (idleTimer != null) clearTimeout(idleTimer);
      idleTimer = setTimeout(emit, IDLE_MS);
      if (points.length >= MAX_POINTS || now - segmentStart >= SEGMENT_MS) emit();
    };
    document.addEventListener("pointermove", handler, { passive: true });
    return () => {
      document.removeEventListener("pointermove", handler);
      if (idleTimer != null) clearTimeout(idleTimer);
    };
  }, []);

  // Scroll depth – trailing-edge throttled; records how far down the page got.
  useEffect(() => {
    const THROTTLE_MS = 1000;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let maxY = 0;
    const handler = () => {
      if (!useAnalytics.getState().capturePointer) return;
      maxY = Math.max(maxY, window.scrollY);
      if (timer != null) return;
      timer = setTimeout(() => {
        timer = null;
        const doc = document.documentElement;
        const total = Math.max(1, doc.scrollHeight - window.innerHeight);
        enqueueEvent({
          event_type: "view",
          event_name: EV.SCROLL_DEPTH,
          path: window.location.pathname,
          properties: {
            kind: "scroll",
            activity: "scroll page",
            y: Math.round(maxY),
            depth_pct: Math.min(100, Math.round((maxY / total) * 100)),
          },
        });
      }, THROTTLE_MS);
    };
    window.addEventListener("scroll", handler, { passive: true });
    return () => {
      window.removeEventListener("scroll", handler);
      if (timer != null) clearTimeout(timer);
    };
  }, []);

  // Clipboard + drag/drop – action and target only, never clipboard contents.
  useEffect(() => {
    const clip = (e: ClipboardEvent) => {
      if (!useAnalytics.getState().captureClicks) return;
      const el = e.target instanceof HTMLElement ? e.target : null;
      if (el?.closest("[data-no-track]")) return;
      const target = el ? elementTarget(el) : null;
      enqueueEvent({
        event_type: "clipboard",
        event_name: EV.CLIPBOARD,
        path: window.location.pathname,
        properties: {
          kind: e.type,
          activity: target ? activityName(e.type, target) : e.type,
          selector: el ? cssPath(el) : null,
          target,
          ui_groups: el ? uiGroups(el) : [],
        },
      });
    };
    const drag = (e: DragEvent) => {
      if (!useAnalytics.getState().captureClicks) return;
      const el = e.target instanceof HTMLElement ? e.target : null;
      if (el?.closest("[data-no-track]")) return;
      const target = el ? elementTarget(el) : null;
      enqueueEvent({
        event_type: "drag",
        event_name: EV.DRAG,
        path: window.location.pathname,
        properties: {
          kind: e.type,
          activity: target ? activityName(e.type, target) : e.type,
          selector: el ? cssPath(el) : null,
          target,
          ui_groups: el ? uiGroups(el) : [],
          x: e.clientX,
          y: e.clientY,
        },
      });
    };
    document.addEventListener("copy", clip, { capture: true });
    document.addEventListener("cut", clip, { capture: true });
    document.addEventListener("paste", clip, { capture: true });
    document.addEventListener("dragstart", drag, { capture: true });
    document.addEventListener("drop", drag, { capture: true });
    return () => {
      document.removeEventListener("copy", clip, { capture: true });
      document.removeEventListener("cut", clip, { capture: true });
      document.removeEventListener("paste", clip, { capture: true });
      document.removeEventListener("dragstart", drag, { capture: true });
      document.removeEventListener("drop", drag, { capture: true });
    };
  }, []);

  // Viewport resizes (debounced) + tab visibility – system-level context.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const onResize = () => {
      if (!useAnalytics.getState().captureClicks) return;
      if (timer != null) clearTimeout(timer);
      timer = setTimeout(() => {
        enqueueEvent({
          event_type: "view",
          event_name: EV.VIEWPORT_RESIZE,
          path: window.location.pathname,
          properties: {
            kind: "resize",
            activity: "resize viewport",
            w: window.innerWidth,
            h: window.innerHeight,
          },
        });
      }, 500);
    };
    const onVisibility = () => {
      if (!useAnalytics.getState().captureClicks) return;
      enqueueEvent({
        event_type: "view",
        event_name: EV.VISIBILITY,
        path: window.location.pathname,
        properties: {
          kind: "visibility",
          activity: `tab ${document.visibilityState}`,
          state: document.visibilityState,
        },
      });
    };
    window.addEventListener("resize", onResize);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibility);
      if (timer != null) clearTimeout(timer);
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
