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

// Mirrors modules/complexity_v2/panel/queries.ts – every Table 3.3 metric
// travels in each slice so the panel can switch the Y-axis metric without
// refetching. Enriched-entropy and some distance metrics are null when the
// slice / log can't support them.
export interface ComplexityMetrics {
  // Entropy
  var_e: number;
  seq_e: number;
  nvar_e: number;
  nseq_e: number;
  // Enriched entropy (null unless the IEEE-XES attribute set is present)
  en_var_e: number | null;
  en_seq_e: number | null;
  en_nvar_e: number | null;
  en_nseq_e: number | null;
  // Size
  n_events: number;
  n_event_types: number;
  n_sequences: number;
  min_seq_len: number;
  avg_seq_len: number;
  max_seq_len: number;
  avg_td_e: number | null;
  // Variation
  n_acyclic_paths: number | null;
  n_acyclic_paths_log10: number;
  n_ties: number;
  lempel_ziv: number;
  n_unique_seq: number;
  perc_unique_seq: number | null;
  avg_distinct_e: number;
  order_var: number | null;
  activity_var: number | null;
  // Distance
  affinity: number | null;
  structure: number | null;
  dev_random: number | null;
  avg_edit_distance: number | null;
  structural_var: number | null;
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
  kind: "complexity_v2_timeseries";
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
  return `/api/v1/modules/complexity_v2_over_time/timeseries?${q}`;
}

export function useComplexityV2Timeseries(
  logId: string,
  mode: SliceMode,
  params: TimeseriesParams,
) {
  return useQuery<ComplexityTimeseries>({
    queryKey: ["modules", "complexity_v2_over_time", "timeseries", logId, mode, params],
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
    queryFn: () =>
      api<Cv4cddResults>(`/api/v1/modules/cv4cdd/results?log_id=${encodeURIComponent(logId)}`),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
    // cv4cdd may not be installed / available for this log → 404. Fail quietly;
    // the chart still renders, just without drift bands.
    retry: false,
  });
}
