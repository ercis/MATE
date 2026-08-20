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
}

const KEY = ["onboarding", "state"] as const;

export function useOnboardingState() {
  return useQuery<OnboardingState>({
    queryKey: KEY,
    queryFn: () => api<OnboardingState>("/api/v1/onboarding"),
    staleTime: 60_000,
  });
}

export function useUpdateOnboarding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: OnboardingState) =>
      api<OnboardingState>("/api/v1/onboarding", { method: "PUT", json: payload }),
    // Reflect the new state immediately so the overlay hides on Finish (and
    // re-appears on Restart) without waiting for the round-trip.
    onMutate: (payload) => qc.setQueryData(KEY, payload),
    onSuccess: (data) => qc.setQueryData(KEY, data),
  });
}
