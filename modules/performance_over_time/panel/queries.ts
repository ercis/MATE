"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

const STALE_TIME = 30_000;

export type SliceMode = "absolute" | "calendar" | "sliding";

export interface TimeseriesParams {
  slices?: number;
  granularity?: string;
  window?: number;
  step?: number;
}

// Mirrors modules/performance/panel/queries.ts – every KPI travels in each
// slice so the panel can switch the Y-axis metric without refetching.
export interface PerfSliceMetrics {
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
}

export interface SlicePoint {
  index: number;
  label: string;
  start: string | null;
  end: string | null;
  n_cases: number;
  n_events: number;
  metrics: PerfSliceMetrics | null;
}

export interface PerformanceTimeseries {
  kind: "performance_timeseries";
  mode: SliceMode;
  params: Record<string, unknown>;
  metric_keys: string[];
  slices: SlicePoint[];
}

function buildUrl(logId: string, mode: SliceMode, params: TimeseriesParams): string {
  const q = new URLSearchParams({ log_id: logId, mode });
  if (mode === "absolute" && params.slices != null) {
    q.set("slices", String(params.slices));
  }
  if (mode === "calendar" && params.granularity) {
    q.set("granularity", params.granularity);
  }
  if (mode === "sliding") {
    if (params.window != null) q.set("window", String(params.window));
    if (params.step != null) q.set("step", String(params.step));
  }
  return `/api/v1/modules/performance_over_time/timeseries?${q}`;
}

export function usePerformanceOverTimeTimeseries(
  logId: string,
  mode: SliceMode,
  params: TimeseriesParams,
) {
  return useQuery<PerformanceTimeseries>({
    queryKey: ["modules", "performance_over_time", "timeseries", logId, mode, params],
    queryFn: () => api<PerformanceTimeseries>(buildUrl(logId, mode, params)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

// ── Concept-drift overlay (data sourced from the cv4cdd module) ───────────────
// We read cv4cdd's existing read-only results endpoint; we never modify cv4cdd.
// A minimal local shape (only the fields the overlay needs) keeps this module
// self-contained even if cv4cdd is absent.

export interface DriftPeriod {
  type: string;
  start_timestamp: string;
  end_timestamp: string;
  confidence: number;
}

export interface Cv4cddResults {
  kind: "cv4cdd_detections";
  drifts: DriftPeriod[];
  n_windows: number;
  confidence_threshold?: number;
  ran?: boolean;
}

export function useDriftPeriods(logId: string) {
  return useQuery<Cv4cddResults>({
    queryKey: ["modules", "cv4cdd", "results", logId],
    queryFn: () => api<Cv4cddResults>(`/api/v1/modules/cv4cdd/results?log_id=${encodeURIComponent(logId)}`),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
    // cv4cdd may not be installed / available for this log → 404. Fail quietly;
    // the chart still renders, just without drift bands.
    retry: false,
  });
}
