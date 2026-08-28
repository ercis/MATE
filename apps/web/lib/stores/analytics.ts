"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

interface AnalyticsState {
  // Mirrors the server `analytics.config.enabled` for fast read on the
  // tracking hot path. The server is still the source of truth and gates
  // ingestion independently.
  enabled: boolean;
  captureClicks: boolean;
  capturePerf: boolean;
  captureErrors: boolean;
  // UI-log capture kinds (input values, keyboard combos, pointer traces).
  captureInputs: boolean;
  captureKeyboard: boolean;
  capturePointer: boolean;
  // True once the onboarding privacy step has been answered (either way).
  promptResolved: boolean;
  // Stable anonymous id; matches `analytics.config.anon_user_id_seed` on
  // the server so a wipe can rotate both sides.
  anonUserId: string | null;
  // Current session id, regenerated after 30 min of inactivity.
  sessionId: string | null;
  sessionStartedAt: number | null;
  lastActivityAt: number | null;
  setEnabled: (v: boolean) => void;
  setCaptureFlags: (
    flags: Partial<
      Pick<
        AnalyticsState,
        | "captureClicks"
        | "capturePerf"
        | "captureErrors"
        | "captureInputs"
        | "captureKeyboard"
        | "capturePointer"
      >
    >,
  ) => void;
  resolvePrompt: (optIn: boolean) => void;
  setAnonUserId: (id: string) => void;
  beginSession: (id: string) => void;
  touchSession: () => void;
  clearSession: () => void;
}

export const useAnalytics = create<AnalyticsState>()(
  persist(
    (set) => ({
      enabled: false,
      captureClicks: true,
      capturePerf: true,
      captureErrors: true,
      captureInputs: true,
      captureKeyboard: true,
      capturePointer: true,
      promptResolved: false,
      anonUserId: null,
      sessionId: null,
      sessionStartedAt: null,
      lastActivityAt: null,
      setEnabled: (v) => set({ enabled: v }),
      setCaptureFlags: (flags) => set(flags),
      resolvePrompt: (optIn) =>
        set({ enabled: optIn, promptResolved: true }),
      setAnonUserId: (id) => set({ anonUserId: id }),
      beginSession: (id) => {
        const now = Date.now();
        set({ sessionId: id, sessionStartedAt: now, lastActivityAt: now });
      },
      touchSession: () => set({ lastActivityAt: Date.now() }),
      clearSession: () =>
        set({ sessionId: null, sessionStartedAt: null, lastActivityAt: null }),
    }),
    {
      name: "ff.analytics",
      storage: createJSONStorage(() => localStorage),
      skipHydration: true,
      // Bump when the persisted shape changes – Zustand discards stored
      // state at older versions, so we avoid a stale `anonUserId: null`
      // (from the original v1 schema) silently dropping every event.
      version: 2,
      // Server-sourced fields (enabled, capture flags, anonUserId) are
      // refetched on every mount by `AnalyticsProvider`. Persisting them
      // would cause stale localStorage values to overwrite the freshly
      // fetched ones after rehydration.
      partialize: (s) => ({
        promptResolved: s.promptResolved,
        sessionId: s.sessionId,
        sessionStartedAt: s.sessionStartedAt,
        lastActivityAt: s.lastActivityAt,
      }),
    },
  ),
);
