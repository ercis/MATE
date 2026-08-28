"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { DfgData } from "@modules/discovery/panel/types";

const STALE_TIME = 30_000;

function perfUrl(path: string, logId: string): string {
  return `/api/v1/modules/performance${path}?log_id=${encodeURIComponent(logId)}`;
}

// Log-level summary (counts + calendar span) – process context shown at the top
// of the panel. Pulled straight from the platform event-log endpoint so the
// numbers match the processes list exactly; the module never recomputes them.
export interface LogSummary {
  cases_count: number | null;
  events_count: number | null;
  variants_count: number | null;
  date_min: string | null;
  date_max: string | null;
}

export interface PerformanceKpis {
  kind: "kpis";
  summary: {
    cases: number;
    events: number;
    variants: number;
    avg_cycle_time_s: number;
    median_cycle_time_s: number;
    p90_cycle_time_s: number;
    p95_cycle_time_s: number;
    min_cycle_time_s: number;
    max_cycle_time_s: number;
    throughput_cases_per_day: number;
    lead_time_s: number;
  };
  per_activity: {
    activity: string;
    frequency: number;
    avg_sojourn_s: number;
    p90_sojourn_s: number;
  }[];
}

export interface BottleneckItem {
  rank: number;
  activity: string;
  frequency: number;
  avg_sojourn_s: number;
  p90_sojourn_s: number;
  share_of_total_time: number;
  histogram: { bucket_min: number; bucket_max: number; count: number }[];
}

export interface BottleneckPayload {
  kind: "bottlenecks";
  items: BottleneckItem[];
}

export interface CycleTimeDistribution {
  kind: "cycle_time_distribution";
  buckets: { bucket: number; count: number; bucket_min: number; bucket_max: number }[];
  stats: {
    avg_cycle_time_s: number;
    median_cycle_time_s: number;
    p90_cycle_time_s: number;
    p95_cycle_time_s: number;
    min_cycle_time_s: number;
    max_cycle_time_s: number;
  };
}

export function useLogSummary(logId: string) {
  return useQuery<LogSummary>({
    queryKey: ["event-logs", "summary", logId],
    queryFn: () => api<LogSummary>(`/api/v1/event-logs/${encodeURIComponent(logId)}`),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function usePerformanceKpis(logId: string) {
  return useQuery<PerformanceKpis>({
    queryKey: ["modules", "performance", "kpis", logId],
    queryFn: () => api<PerformanceKpis>(perfUrl("/kpis", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function usePerformanceBottlenecks(logId: string) {
  return useQuery<BottleneckPayload>({
    queryKey: ["modules", "performance", "bottlenecks", logId],
    queryFn: () => api<BottleneckPayload>(perfUrl("/bottlenecks", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function usePerformanceDfg(logId: string) {
  return useQuery<DfgData>({
    queryKey: ["modules", "performance", "dfg", logId],
    queryFn: () => api<DfgData>(perfUrl("/dfg-performance", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function usePerformanceCycleTimeDistribution(logId: string) {
  return useQuery<CycleTimeDistribution>({
    queryKey: ["modules", "performance", "cycle-time", logId],
    queryFn: () => api<CycleTimeDistribution>(perfUrl("/cycle-time-distribution", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}
