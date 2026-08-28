"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

export type OnboardingMode = "force" | "on" | "off";

export interface AnalyticsConfig {
  enabled: boolean;
  retention_days: number | null;
  capture_clicks: boolean;
  capture_perf: boolean;
  capture_errors: boolean;
  // UI-log capture kinds (Abb & Rehse model): committed input values,
  // keyboard combos, and sampled pointer traces / scroll depth.
  capture_inputs: boolean;
  capture_keyboard: boolean;
  capture_pointer: boolean;
  opted_in_at: string | null;
  anon_user_id_seed: string;
  // Server-side policy from USER_TRACKING_ONBOARDING; read-only on the client.
  // `force` hides the privacy step/tab and keeps tracking on for everyone.
  onboarding_mode: OnboardingMode;
}

export interface AnalyticsTypeCount {
  event_type: string;
  count: number;
}

export interface AnalyticsSummary {
  enabled: boolean;
  total_events: number;
  total_sessions: number;
  sessions_last_30d: number;
  oldest_event: string | null;
  newest_event: string | null;
  by_type: AnalyticsTypeCount[];
}

export interface WipeResponse {
  deleted_events: number;
  deleted_sessions: number;
  new_anon_user_id_seed: string;
}

const KEYS = {
  config: ["analytics", "config"] as const,
  summary: ["analytics", "summary"] as const,
};

export function useAnalyticsConfig() {
  return useQuery<AnalyticsConfig>({
    queryKey: KEYS.config,
    queryFn: () => api<AnalyticsConfig>("/api/v1/usage/config"),
    staleTime: 30_000,
  });
}

export function useUpdateAnalyticsConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AnalyticsConfig) =>
      api<AnalyticsConfig>("/api/v1/usage/config", {
        method: "PUT",
        json: payload,
      }),
    onSuccess: (data) => {
      qc.setQueryData(KEYS.config, data);
      qc.invalidateQueries({ queryKey: KEYS.summary });
    },
  });
}

export function useAnalyticsSummary() {
  return useQuery<AnalyticsSummary>({
    queryKey: KEYS.summary,
    queryFn: () => api<AnalyticsSummary>("/api/v1/usage/summary"),
    staleTime: 5_000,
  });
}

export function useWipeAnalytics() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<WipeResponse>("/api/v1/usage/sync", { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEYS.summary });
      qc.invalidateQueries({ queryKey: KEYS.config });
    },
  });
}
