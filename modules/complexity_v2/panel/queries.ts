"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

const STALE_TIME = 30_000;

function url(path: string, logId: string): string {
  const q = new URLSearchParams({ log_id: logId });
  return `/api/v1/modules/complexity_v2${path}?${q}`;
}

export interface MetricItem {
  key: string;
  label: string;
  name: string;
  category: string;
  source: string;
  description: string;
  value: number | null;
}

export interface MetricGroup {
  category: string;
  items: MetricItem[];
}

export interface ComplexityV2Payload {
  kind: "complexity_v2";
  values: Record<string, number | null>;
  groups: MetricGroup[];
  enriched_supported: boolean;
  downsampled: boolean;
  n_events: number | null;
  n_cases: number | null;
  n_variants: number | null;
  distance_variants_used: number | null;
  max_variants: number | null;
}

export interface TransitionMatrix {
  activities: string[];
  matrix: number[][];
  truncated: boolean;
}

export function useComplexityV2(logId: string) {
  return useQuery<ComplexityV2Payload>({
    queryKey: ["modules", "complexity_v2", "metrics", logId],
    queryFn: () => api<ComplexityV2Payload>(url("/metrics", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function useTransitionMatrix(logId: string) {
  return useQuery<TransitionMatrix>({
    queryKey: ["modules", "complexity_v2", "transition-matrix", logId],
    queryFn: () => api<TransitionMatrix>(url("/transition-matrix", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

// ── Shared value formatting (paper-faithful units) ────────────────────────────

const INT_KEYS = new Set([
  "n_events",
  "n_event_types",
  "n_sequences",
  "min_seq_len",
  "max_seq_len",
  "n_ties",
  "lempel_ziv",
  "n_unique_seq",
]);

export function formatMetric(
  key: string,
  value: number | null,
  values: Record<string, number | null>,
): string {
  if (key === "n_acyclic_paths") return formatAcyclicPaths(value, values);
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";

  if (key === "avg_td_e") return formatSeconds(value);
  if (key === "perc_unique_seq") return `${value.toFixed(2)} %`;
  if (INT_KEYS.has(key)) return Math.round(value).toLocaleString();
  if (key === "avg_seq_len" || key === "avg_distinct_e") return value.toFixed(2);
  // Entropy / variation / distance scalars.
  return Math.abs(value) >= 1000 ? value.toLocaleString(undefined, { maximumFractionDigits: 1 }) : value.toFixed(3);
}

function formatAcyclicPaths(
  value: number | null,
  values: Record<string, number | null>,
): string {
  if (value !== null && value !== undefined && Number.isFinite(value)) {
    return value >= 1e6 ? value.toExponential(2) : Math.round(value).toLocaleString();
  }
  const log10 = values["n_acyclic_paths_log10"];
  if (log10 !== null && log10 !== undefined && Number.isFinite(log10)) {
    return `≈10^${log10.toFixed(1)}`;
  }
  return "–";
}

function formatSeconds(s: number): string {
  if (s < 60) return `${s.toFixed(1)} s`;
  if (s < 3600) return `${(s / 60).toFixed(1)} min`;
  if (s < 86400) return `${(s / 3600).toFixed(1)} h`;
  return `${(s / 86400).toFixed(1)} d`;
}
