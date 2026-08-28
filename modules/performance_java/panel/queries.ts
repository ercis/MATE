"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

const STALE_TIME = 30_000;

function routeUrl(path: string, logId: string): string {
  return `/api/v1/modules/performance_java${path}?log_id=${encodeURIComponent(logId)}`;
}

/**
 * The Java worker omits a metric entirely when the log can't support it (no
 * `end_timestamp` -> no processing time, no `resource` -> no actor count), so
 * every figure past the core four is optional. Render what arrived; never
 * substitute a zero for a metric that was never computed.
 */
export interface PerformanceKpis {
  kind: string;
  cases?: number;
  events?: number;
  activities?: number;
  variants?: number;
  events_per_case?: number;
  cycle_time_avg_seconds?: number;
  cycle_time_median_seconds?: number;
  cycle_time_p90_seconds?: number;
  cycle_time_max_seconds?: number;
  log_span_days?: number;
  throughput_cases_per_day?: number;
  processing_time_avg_seconds?: number;
  waiting_time_share?: number;
  resources?: number;
}

export interface ActivityRow {
  activity: string;
  occurrences?: number;
  cases?: number;
  total_dwell_seconds?: number;
  avg_dwell_seconds?: number;
  median_dwell_seconds?: number;
  p90_dwell_seconds?: number;
  avg_processing_seconds?: number;
  dwell_share?: number;
}

export interface TransitionRow {
  from_activity: string;
  to_activity: string;
  occurrences?: number;
  cases?: number;
  total_wait_seconds?: number;
  avg_wait_seconds?: number;
  median_wait_seconds?: number;
  p90_wait_seconds?: number;
  wait_share?: number;
}

export interface ActivitiesPayload {
  activities: ActivityRow[];
  kind: string;
  row_count: number;
  /** The worker caps its output; true means the tail was cut. */
  truncated: boolean;
}

export interface TransitionsPayload {
  transitions: TransitionRow[];
  kind: string;
  row_count: number;
  truncated: boolean;
}

export function usePerformanceKpis(logId: string) {
  return useQuery<PerformanceKpis>({
    queryKey: ["modules", "performance_java", "kpis", logId],
    queryFn: () => api<PerformanceKpis>(routeUrl("/kpis", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function usePerformanceActivities(logId: string) {
  return useQuery<ActivitiesPayload>({
    queryKey: ["modules", "performance_java", "activities", logId],
    queryFn: () => api<ActivitiesPayload>(routeUrl("/activities", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function usePerformanceTransitions(logId: string) {
  return useQuery<TransitionsPayload>({
    queryKey: ["modules", "performance_java", "transitions", logId],
    queryFn: () => api<TransitionsPayload>(routeUrl("/transitions", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}
