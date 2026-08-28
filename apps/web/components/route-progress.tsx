"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";

// A thin top progress bar that gives instant feedback on *every* navigation –
// the App Router otherwise stalls on the current screen until the next route's
// payload resolves, with no visible signal that the click registered.
//
// Coverage is two-pronged:
//  1. A capture-phase document click listener catches every internal <a>/<Link>
//     click app-wide (no need to wrap each link).
//  2. `routeProgress.start()` lets programmatic navigations (router.push inside
//     a transition, e.g. the sidebar) nudge the same bar.
// The bar completes when the resolved pathname/searchParams actually change, with
// a safety timeout so it can never get stuck on a no-op navigation.

const startListeners = new Set<() => void>();

// A navigation faster than this never shows the bar (pre-warmed routes resolve in
// ~tens of ms). Only genuinely slow navigations cross the threshold and show it.
const SHOW_DELAY_MS = 180;

/** Imperatively begin the bar – for programmatic `router.push` navigations. */
export const routeProgress = {
  start() {
    startListeners.forEach((l) => l());
  },
};

export function RouteProgress() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [progress, setProgress] = useState(0);
  const [visible, setVisible] = useState(false);

  const activeRef = useRef(false);
  const trickle = useRef<ReturnType<typeof setInterval> | null>(null);
  const safety = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hide = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Defer showing the bar: a fast (pre-warmed) nav resolves before this fires, so
  // the bar never flashes. `shownRef` tracks whether it actually became visible.
  const show = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shownRef = useRef(false);

  const finish = useCallback(() => {
    if (!activeRef.current) return;
    activeRef.current = false;
    // Nav resolved before the show-delay elapsed → the bar never appeared, so
    // there's nothing to flash. This is the common case for pre-warmed routes.
    if (show.current) {
      clearTimeout(show.current);
      show.current = null;
    }
    if (trickle.current) {
      clearInterval(trickle.current);
      trickle.current = null;
    }
    if (safety.current) {
      clearTimeout(safety.current);
      safety.current = null;
    }
    if (!shownRef.current) return;
    setProgress(100);
    hide.current = setTimeout(() => {
      setVisible(false);
      setProgress(0);
      shownRef.current = false;
    }, 250);
  }, []);

  const start = useCallback(() => {
    if (activeRef.current) return;
    activeRef.current = true;
    if (hide.current) {
      clearTimeout(hide.current);
      hide.current = null;
    }
    // Only reveal the bar if the nav is still pending after the delay.
    show.current = setTimeout(() => {
      shownRef.current = true;
      setVisible(true);
      setProgress(8);
      // Ease toward 90% – never reach 100 until the route actually resolves.
      trickle.current = setInterval(() => {
        setProgress((p) => (p >= 90 ? 90 : p + (90 - p) * 0.12));
      }, 200);
    }, SHOW_DELAY_MS);
    safety.current = setTimeout(finish, 8000);
  }, [finish]);

  // Register the imperative trigger + the global anchor-click listener.
  useEffect(() => {
    startListeners.add(start);
    const onClick = (e: MouseEvent) => {
      if (
        e.defaultPrevented ||
        e.button !== 0 ||
        e.metaKey ||
        e.ctrlKey ||
        e.shiftKey ||
        e.altKey
      ) {
        return;
      }
      const anchor = (e.target as HTMLElement | null)?.closest?.("a");
      const href = anchor?.getAttribute("href");
      if (!href || anchor?.target === "_blank" || anchor?.hasAttribute("download")) return;
      if (href.startsWith("#") || /^(mailto|tel):/.test(href)) return;
      let url: URL;
      try {
        url = new URL(href, window.location.href);
      } catch {
        return;
      }
      if (url.origin !== window.location.origin) return;
      // Same URL → the router no-ops, so the bar would never complete.
      if (url.pathname === window.location.pathname && url.search === window.location.search) {
        return;
      }
      start();
    };
    document.addEventListener("click", onClick, true);
    return () => {
      startListeners.delete(start);
      document.removeEventListener("click", onClick, true);
    };
  }, [start]);

  // The route resolved – complete the bar. Skips the initial mount via activeRef.
  useEffect(() => {
    finish();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, searchParams]);

  // Clean up timers on unmount.
  useEffect(() => {
    return () => {
      if (show.current) clearTimeout(show.current);
      if (trickle.current) clearInterval(trickle.current);
      if (safety.current) clearTimeout(safety.current);
      if (hide.current) clearTimeout(hide.current);
    };
  }, []);

  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-x-0 top-0 z-[100] h-0.5 transition-opacity duration-300"
      style={{ opacity: visible ? 1 : 0 }}
    >
      <div
        className="h-full bg-primary shadow-[0_0_10px] shadow-primary/60 transition-[width] duration-200 ease-out"
        style={{ width: `${progress}%` }}
      />
    </div>
  );
}
