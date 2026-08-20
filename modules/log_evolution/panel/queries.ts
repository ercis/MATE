"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

const STALE_TIME = 30_000;

export type Granularity = "auto" | "daily" | "weekly" | "monthly" | "quarterly" | "yearly";

// Per-period volume bundle – one payload feeds the arrivals/WIP/activity-mix
// widgets so they share a single cached fetch. Mirrors the backend
// `compute_evolution` schema (modules/log_evolution/evolution.py).
export interface ActivityMix {
  activities: string[];
  series: number[][]; // one row per activity, aligned to `periods`
}

export interface LogEvolution {
  kind: "log_evolution";
  granularity: string;
  freq: string;
  periods: string[];
  arrivals: number[];
  completions: number[];
  active: number[];
  events: number[];
  activity_mix: ActivityMix;
}

export interface DottedPoint {
  y: number; // case rank (cases ordered by start time)
  t: number; // event time, epoch milliseconds
  a: number; // index into `activities`
}

export interface LogDotted {
  kind: "log_dotted";
  total_events: number;
  sampled: boolean;
  max_points: number;
  n_cases: number;
  activities: string[];
  points: DottedPoint[];
}

export function useEvolution(logId: string, granularity: Granularity) {
  return useQuery<LogEvolution>({
    queryKey: ["modules", "log_evolution", "timeseries", logId, granularity],
    queryFn: () =>
      api<LogEvolution>(
        `/api/v1/modules/log_evolution/timeseries?log_id=${encodeURIComponent(
          logId,
        )}&granularity=${granularity}`,
      ),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function useDotted(logId: string, maxPoints: number) {
  return useQuery<LogDotted>({
    queryKey: ["modules", "log_evolution", "dotted", logId, maxPoints],
    queryFn: () =>
      api<LogDotted>(
        `/api/v1/modules/log_evolution/dotted?log_id=${encodeURIComponent(
          logId,
        )}&max_points=${maxPoints}`,
      ),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

// ── shared chart helpers ──────────────────────────────────────────────────────

// Categorical palette for stacked activities / dotted-chart legend. Uses the
// host theme's chart tokens (defined in apps/web globals) so colours match the
// rest of the app, with a hashed fallback for indices beyond the token set.
export const CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "#6366f1",
  "#ec4899",
  "#14b8a6",
  "#f59e0b",
  "#8b5cf6",
  "#ef4444",
  "#22c55e",
  "#94a3b8", // "Other" tends to land last → muted grey
];

export function colorAt(i: number): string {
  return CHART_COLORS[i % CHART_COLORS.length];
}

// Short axis label for an epoch-ms tick on the dotted chart.
export function formatDay(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;
}
