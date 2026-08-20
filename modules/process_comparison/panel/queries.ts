"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  ActivityDeltasData,
  DfgDiffData,
  LogSummary,
  SimilarityData,
  VariantDiffData,
} from "./types";

const STALE_TIME = 30_000;
const MOD = "/api/v1/modules/process_comparison";

function url(path: string, params: Record<string, string>): string {
  const search = new URLSearchParams(params);
  return `${MOD}${path}?${search.toString()}`;
}

/** Ready case-centric logs the user can compare against (excludes OCEL). */
export function useComparisonLogs() {
  return useQuery<LogSummary[]>({
    queryKey: ["modules", "process_comparison", "logs"],
    queryFn: () => api<LogSummary[]>("/api/v1/event-logs?status=ready"),
    staleTime: STALE_TIME,
    select: (logs) => logs.filter((l) => l.log_model === "case_centric" && l.status === "ready"),
  });
}

export function useSimilarity(logId: string, others: string[]) {
  return useQuery<SimilarityData>({
    queryKey: ["modules", "process_comparison", "similarity", logId, [...others].sort()],
    queryFn: () => api<SimilarityData>(url("/similarity", { log_id: logId, others: others.join(",") })),
    enabled: Boolean(logId) && others.length > 0,
    staleTime: STALE_TIME,
  });
}

export function useDfgOverlay(logId: string, other: string | null) {
  return useQuery<DfgDiffData>({
    queryKey: ["modules", "process_comparison", "dfg-overlay", logId, other],
    queryFn: () => api<DfgDiffData>(url("/dfg-overlay", { log_id: logId, other: other ?? "" })),
    enabled: Boolean(logId) && Boolean(other),
    staleTime: STALE_TIME,
  });
}

export function useVariantDiff(logId: string, others: string[]) {
  return useQuery<VariantDiffData>({
    queryKey: ["modules", "process_comparison", "variants", logId, [...others].sort()],
    queryFn: () => api<VariantDiffData>(url("/variants", { log_id: logId, others: others.join(",") })),
    enabled: Boolean(logId) && others.length > 0,
    staleTime: STALE_TIME,
  });
}

export function useActivityDeltas(logId: string, others: string[]) {
  return useQuery<ActivityDeltasData>({
    queryKey: ["modules", "process_comparison", "activity-deltas", logId, [...others].sort()],
    queryFn: () =>
      api<ActivityDeltasData>(url("/activity-deltas", { log_id: logId, others: others.join(",") })),
    enabled: Boolean(logId) && others.length > 0,
    staleTime: STALE_TIME,
  });
}
