"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { ExperienceLevel } from "@/lib/stores/onboarding";

/**
 * Per-user onboarding state, server-backed (UserSetting key `onboarding`).
 *
 * Source of truth for whether the welcome overlay shows. Unlike the old
 * localStorage flag, this is keyed by Keycloak user, so a new account always
 * sees onboarding and a finished account never re-sees it across browsers.
 */
export interface OnboardingState {
  completed: boolean;
  experience_level: ExperienceLevel | null;
  /** Interactive product tour (the high-level platform walkthrough)
   *  finished/skipped. Separate from `completed` so the tour can auto-chain
   *  after the wizard yet be replayed without re-opening it. */
  tour_completed: boolean;
}

const KEY = ["onboarding", "state"] as const;

export function useOnboardingState() {
  return useQuery<OnboardingState>({
    queryKey: KEY,
    queryFn: () => api<OnboardingState>("/api/v1/onboarding"),
    staleTime: 60_000,
  });
}

const DEFAULTS: OnboardingState = {
  completed: false,
  experience_level: null,
  tour_completed: false,
};

export function useUpdateOnboarding() {
  const qc = useQueryClient();
  return useMutation({
    // Accepts a partial patch – the API merges it server-side (exclude_unset),
    // so e.g. `{ tour_completed: true }` won't reset `completed`/`experience_level`.
    mutationFn: (payload: Partial<OnboardingState>) =>
      api<OnboardingState>("/api/v1/onboarding", { method: "PUT", json: payload }),
    // Reflect the new state immediately so the overlay hides on Finish (and
    // re-appears on Restart) without waiting for the round-trip. Merge over the
    // previous cache so a partial patch doesn't drop sibling fields.
    onMutate: (payload) =>
      qc.setQueryData<OnboardingState>(KEY, (prev) => ({ ...(prev ?? DEFAULTS), ...payload })),
    onSuccess: (data) => qc.setQueryData(KEY, data),
  });
}
