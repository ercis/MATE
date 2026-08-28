"use client";

import { create } from "zustand";

interface UiState {
  // Left nav sidebar. `false` (default) = auto mode: retracted rail that
  // expands while the cursor is over it. `true` = locked maximized (pinned
  // open regardless of hover). The transient hover state is component-local.
  sidebarPinned: boolean;
  showUnavailableModules: boolean;
  showDisabledModules: boolean;
  // When on, only modules that declare `isConfidentialSafe: true` in their
  // manifest are made available (Settings → General). Off by default.
  confidentialOnly: boolean;
  notificationsMuted: boolean;
  mateOpen: boolean;
  // Locale + import defaults – used by Settings → General and pre-filled
  // into the CSV import form so users don't re-pick them every upload
  // (§7.6.1).
  timezone: string;
  dateFormat: "iso" | "us" | "eu";
  csvDelimiter: "," | ";" | "\t" | "|";
  csvTimestampFormat: string;
  // Topbar back-arrow behavior (Settings → General). "history" (default) =
  // router.back(); "parent" = up one level in the breadcrumb hierarchy.
  backButtonMode: "history" | "parent";
  toggleSidebarPinned: () => void;
  setSidebarPinned: (v: boolean) => void;
  setShowUnavailableModules: (v: boolean) => void;
  setShowDisabledModules: (v: boolean) => void;
  setConfidentialOnly: (v: boolean) => void;
  setNotificationsMuted: (v: boolean) => void;
  toggleMate: () => void;
  setMateOpen: (v: boolean) => void;
  setTimezone: (v: string) => void;
  setDateFormat: (v: "iso" | "us" | "eu") => void;
  setCsvDelimiter: (v: "," | ";" | "\t" | "|") => void;
  setCsvTimestampFormat: (v: string) => void;
  setBackButtonMode: (v: "history" | "parent") => void;
  // Replace the data slice with a server blob merged over defaults. Used by
  // the per-user server-state sync (see `lib/server-persist.ts`).
  hydrate: (data: Record<string, unknown>) => void;
}

const DEFAULT_TIMEZONE = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
})();

/** Data defaults (no actions). Spread into the store and used to reset on
 *  account switch so one user's prefs never leak into another's session. */
export const UI_DEFAULTS = {
  sidebarPinned: false,
  showUnavailableModules: true,
  showDisabledModules: false,
  confidentialOnly: false,
  notificationsMuted: false,
  mateOpen: false,
  timezone: DEFAULT_TIMEZONE,
  dateFormat: "iso" as const,
  csvDelimiter: "," as const,
  csvTimestampFormat: "",
  backButtonMode: "history" as const,
};

export const useUi = create<UiState>((set) => ({
  ...UI_DEFAULTS,
  toggleSidebarPinned: () => set((s) => ({ sidebarPinned: !s.sidebarPinned })),
  setSidebarPinned: (v) => set({ sidebarPinned: v }),
  setShowUnavailableModules: (v) => set({ showUnavailableModules: v }),
  setShowDisabledModules: (v) => set({ showDisabledModules: v }),
  setConfidentialOnly: (v) => set({ confidentialOnly: v }),
  setNotificationsMuted: (v) => set({ notificationsMuted: v }),
  toggleMate: () => set((s) => ({ mateOpen: !s.mateOpen })),
  setMateOpen: (v) => set({ mateOpen: v }),
  setTimezone: (v) => set({ timezone: v }),
  setDateFormat: (v) => set({ dateFormat: v }),
  setCsvDelimiter: (v) => set({ csvDelimiter: v }),
  setCsvTimestampFormat: (v) => set({ csvTimestampFormat: v }),
  setBackButtonMode: (v) => set({ backButtonMode: v }),
  hydrate: (data) => set({ ...UI_DEFAULTS, ...(data as Partial<UiState>) }),
}));
