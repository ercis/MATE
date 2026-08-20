"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

const STALE_TIME = 30_000;

function url(path: string, logId: string): string {
  const q = new URLSearchParams({ log_id: logId });
  return `/api/v1/modules/complexity${path}?${q}`;
}

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

export interface ComplexityPayload {
  kind: "complexity_metrics";
  basic: ComplexityMetrics;
  enriched: ComplexityMetrics | null;
  enriched_supported: boolean;
  exponential_k: number;
}

export function useComplexityMetrics(logId: string) {
  return useQuery<ComplexityPayload>({
    queryKey: ["modules", "complexity", "metrics", logId],
    queryFn: () => api<ComplexityPayload>(url("/metrics", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}
