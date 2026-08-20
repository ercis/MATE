"use client";

/**
 * Shared types + the `/results` query used by both the panel and the widgets.
 * Mirrors the JSON returned by `GET /api/v1/modules/agentsimulator/results`.
 */

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

export type MetricKey = "NGD" | "AEDD" | "CEDD" | "REDD" | "CTDD";

export interface MetricCell {
  mean: number | null;
  std: number | null;
  values: number[];
  label: string;
  lower_better: boolean;
}

export interface DurationStats {
  mean_h: number;
  median_h: number;
  p90_h: number;
}

export interface AgentSimResult {
  status: "ready" | "empty";
  generated_at?: string;
  runtime_seconds?: number;
  params?: {
    num_simulations: number;
    mode: string;
    central_orchestration: boolean;
    extr_delays: boolean;
    determine_automatically: boolean;
  };
  input?: { events: number; cases: number };
  metrics?: Record<MetricKey, MetricCell>;
  simulation?: { num_logs: number; avg_cases: number; avg_events: number };
  test?: { cases: number; events: number; activities: number; resources: number };
  cycle_time?: {
    unit: string;
    bins: { label: string; real: number; sim: number }[];
    real_stats: DurationStats;
    sim_stats: DurationStats;
  };
  arrivals?: { unit: string; series: { t: number; real: number; sim: number }[] };
  circadian?: { hour: number; real: number; sim: number }[];
  activities?: { activity: string; real: number; sim: number }[];
  handover?: { resources: string[]; real: number[][]; sim: number[][] };
  preview?: { columns: string[]; rows: string[][]; total: number };
}

export const METRIC_ORDER: MetricKey[] = ["NGD", "AEDD", "CEDD", "REDD", "CTDD"];

// AgentSimulator's result is a property of the *whole* log: a run train/test-
// splits the entire log and can only be started (unfiltered) from the panel. On
// a filtered dashboard, `api()` would otherwise attach the board's ambient
// `X-FF-Event-Filter`, routing reads into a per-filter cache *variant* the run
// never wrote to – so the cards render empty. Pin every agentsimulator call to
// the canonical (no-filter) namespace by sending a no-op filter header: `api()`
// leaves a caller-set header untouched (so this overrides the ambient one), and
// the backend decodes an empty filter list to "no override". Keep run + reads on
// the same namespace so what the panel writes is exactly what the cards read.
function encodeEmptyFilter(): string {
  const bytes = new TextEncoder().encode(JSON.stringify({ filter: [] }));
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

export const CANONICAL_RESULT_HEADERS: Record<string, string> = {
  "X-FF-Event-Filter": encodeEmptyFilter(),
};

export function useAgentSimResults(logId: string) {
  return useQuery<AgentSimResult>({
    queryKey: ["modules", "agentsimulator", "results", logId],
    queryFn: () =>
      api(`/api/v1/modules/agentsimulator/results?log_id=${encodeURIComponent(logId)}`, {
        headers: CANONICAL_RESULT_HEADERS,
      }),
    enabled: Boolean(logId),
    staleTime: 30_000,
  });
}
