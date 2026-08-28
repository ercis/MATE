"use client";

import { create } from "zustand";
import { useUi } from "@/lib/stores/ui";

/**
 * Run state for the interactive product tour - the live spotlight walkthrough of
 * the platform's shape (where data lives, how a log becomes an analysis). It
 * stays high-level on purpose and never drills into a single module.
 *
 * Completion is persisted server-side via the `onboarding` UserSetting
 * (`tour_completed`); this store only holds the ephemeral run state so any
 * component can launch/drive it (`TourOverlay` renders it, Settings → About
 * starts it, the wizard auto-chains into it).
 */
interface TourState {
  active: boolean;
  stepIndex: number;
  /** True when auto-launched right after the setup wizard (vs. a manual replay).
   *  Informational – lets the overlay tune copy/behaviour if needed. */
  auto: boolean;
  /**
   * The log to walk the tour through. Set by the wizard to the log it just
   * queued, so a brand-new user's own upload is spotlighted even though it is
   * still importing. `null` → the overlay falls back to the newest usable log.
   */
  logId: string | null;
  start: (opts?: { auto?: boolean; logId?: string | null }) => void;
  next: () => void;
  prev: () => void;
  goTo: (i: number) => void;
  stop: () => void;
}

// The sidebar is collapsed-on-hover by default, and the tour's full-screen click
// blocker eats the hover that would expand it - the "here's the nav" step would
// spotlight a bare icon strip. Pin it for the duration and restore the user's
// own setting afterwards.
let railWasPinned: boolean | null = null;

function pinRail() {
  const ui = useUi.getState();
  railWasPinned = ui.sidebarPinned;
  if (!ui.sidebarPinned) ui.setSidebarPinned(true);
}

function restoreRail() {
  if (railWasPinned === null) return;
  const wanted = railWasPinned;
  railWasPinned = null;
  const ui = useUi.getState();
  if (ui.sidebarPinned !== wanted) ui.setSidebarPinned(wanted);
}

export const useTour = create<TourState>((set) => ({
  active: false,
  stepIndex: 0,
  auto: false,
  logId: null,
  start: (opts) => {
    pinRail();
    set({
      active: true,
      stepIndex: 0,
      auto: opts?.auto ?? false,
      logId: opts?.logId ?? null,
    });
  },
  next: () => set((s) => ({ stepIndex: s.stepIndex + 1 })),
  prev: () => set((s) => ({ stepIndex: Math.max(0, s.stepIndex - 1) })),
  goTo: (i) => set({ stepIndex: Math.max(0, i) }),
  stop: () => {
    restoreRail();
    set({ active: false, stepIndex: 0, auto: false, logId: null });
  },
}));
