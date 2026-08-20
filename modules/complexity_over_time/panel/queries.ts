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

// Mirrors modules/complexity/panel/queries.ts – every KPI travels in each
// slice so the panel can switch the Y-axis metric without refetching.
export interface ComplexityMetrics {
  magnitude: number;
  support: number;
  variety: number;
  level_of_detail: number;
  time_granularity_s: number;
  structure: number | null;
  affinity: number | null;
  trace_length_min: number;
  trace_length_avg: number;
  trace_length_max: number;
  distinct_traces_pct: number;
  deviation_from_random: number | null;
  lempel_ziv: number;
  pentland_task: number;
  pentland_process: number;
  variant_entropy: number;
  normalized_variant_entropy: number;
  sequence_entropy: number;
  normalized_sequence_entropy: number;
  sequence_entropy_linear: number;
  normalized_sequence_entropy_linear: number;
  sequence_entropy_exponential: number;
  normalized_sequence_entropy_exponential: number;
  exponential_k: number;
}

export interface SlicePoint {
  index: number;
  label: string;
  start: string | null;
  end: string | null;
  n_cases: number;
  n_events: number;
  metrics: ComplexityMetrics | null;
}

export interface ComplexityTimeseries {
  kind: "complexity_timeseries";
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
  return `/api/v1/modules/complexity_over_time/timeseries?${q}`;
}

export function useComplexityTimeseries(
  logId: string,
  mode: SliceMode,
  params: TimeseriesParams,
) {
  return useQuery<ComplexityTimeseries>({
    queryKey: ["modules", "complexity_over_time", "timeseries", logId, mode, params],
    queryFn: () => api<ComplexityTimeseries>(buildUrl(logId, mode, params)),
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
