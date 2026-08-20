"use client";

import { create } from "zustand";

export type ExperienceLevel = "beginner" | "intermediate" | "expert";

/**
 * Transient onboarding UI state – only the in-flight experience-level
 * selection. Whether onboarding is *completed* now lives per-user on the
 * server (see `lib/onboarding-queries.ts`), not in browser localStorage, so a
 * second account on the same browser still gets the welcome flow.
 */
interface OnboardingState {
  experienceLevel: ExperienceLevel | null;
  setExperienceLevel: (level: ExperienceLevel) => void;
  clear: () => void;
}

export const useOnboarding = create<OnboardingState>((set) => ({
  experienceLevel: null,
  setExperienceLevel: (level) => set({ experienceLevel: level }),
  clear: () => set({ experienceLevel: null }),
}));
