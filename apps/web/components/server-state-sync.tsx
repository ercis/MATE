"use client";

import { useSession } from "next-auth/react";

import { useUi } from "@/lib/stores/ui";
import { useVizSettings } from "@/lib/stores/visualization-settings";
import { useServerPersistedStore, pickData } from "@/lib/server-persist";

/**
 * Bridges the device-local zustand stores (`useUi`, `useVizSettings`) to
 * per-user server state. Renders nothing; mounted once under SessionProvider.
 *
 * Hydrates each store from the server on sign-in and debounce-saves changes
 * back, so these prefs are per-account (and follow the user across browsers)
 * instead of bleeding between accounts via shared localStorage.
 */
export function ServerStateSync() {
  const { data: session, status } = useSession();
  const enabled = status === "authenticated";
  // Re-hydrate when the signed-in account changes.
  const userKey = session?.user?.email ?? null;

  useServerPersistedStore({
    store: useUi,
    key: "ui",
    enabled,
    userKey,
    debounceMs: 500,
    read: () => pickData(useUi.getState()),
    apply: (data) => useUi.getState().hydrate(data),
    legacyKey: "ff.ui",
  });

  useServerPersistedStore({
    store: useVizSettings,
    key: "viz",
    enabled,
    userKey,
    // Viz settings churn fast (slider/node drags) – a longer debounce keeps
    // the save rate sane; the tab-hide flush catches the final edit.
    debounceMs: 1000,
    read: () => pickData(useVizSettings.getState()),
    apply: (data) => useVizSettings.getState().hydrate(data),
    legacyKey: "ff.viz-settings.v1",
  });

  return null;
}
