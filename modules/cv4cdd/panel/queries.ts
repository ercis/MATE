"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

export interface Cv4cddDrift {
  type: "sudden" | "gradual" | "incremental" | "recurring" | string;
  start_timestamp: string;
  end_timestamp: string;
  start_window: number;
  end_window: number;
  confidence: number;
  bbox: [number, number, number, number];
}

export interface Cv4cddResults {
  kind: "cv4cdd_detections";
  drifts: Cv4cddDrift[];
  n_windows: number;
  confidence_threshold?: number;
  ran?: boolean;
}

const KEYS = {
  results: (logId: string) => ["modules", "cv4cdd", "results", logId] as const,
};

function url(path: string, logId: string): string {
  const q = new URLSearchParams({ log_id: logId });
  return `/api/v1/modules/cv4cdd${path}?${q}`;
}

export function useCv4cddResults(logId: string) {
  return useQuery<Cv4cddResults>({
    queryKey: KEYS.results(logId),
    queryFn: () => api<Cv4cddResults>(url("/results", logId)),
    enabled: Boolean(logId),
    staleTime: 5_000,
  });
}

/** POST /detect → returns `{ job_id }`. The platform fires
 *  `job.completed` on the WebSocket bus when finished; the panel
 *  watches that to refetch results. */
export function useRunCv4cdd(logId: string) {
  const qc = useQueryClient();
  return useMutation<{ job_id: string }, Error, void>({
    mutationFn: () =>
      api<{ job_id: string }>(url("/detect", logId), { method: "POST" }),
    onSuccess: () => {
      // No-op here – completion polling lives in the panel so the user
      // sees the running state and the toast lands at the same moment.
      void qc.invalidateQueries({ queryKey: KEYS.results(logId) });
    },
  });
}

export const cv4cddQueryKeys = KEYS;
