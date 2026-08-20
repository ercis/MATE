"use client";

import { useCallback, useEffect, useRef } from "react";

import { trackCustom } from "@/lib/analytics/client";
import type { EventName } from "@/lib/analytics/events";

/**
 * Returns a stable `track(name, props?)` function. Safe to call before the
 * provider has resolved – the client gates on the store internally.
 */
export function useTrack() {
  return useCallback(
    (name: EventName | string, properties?: Record<string, unknown>) => {
      trackCustom(name, properties);
    },
    [],
  );
}

/** Fires the given event exactly once per mount lifecycle. */
export function useTrackOnce(
  name: EventName | string,
  properties?: Record<string, unknown>,
) {
  const fired = useRef(false);
  useEffect(() => {
    if (fired.current) return;
    fired.current = true;
    trackCustom(name, properties);
    // The properties object is captured once on first mount intentionally;
    // re-firing on every change would defeat the "once" semantics.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);
}
