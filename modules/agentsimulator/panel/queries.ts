"use client";

/**
 * Shared types + the `/results` query used by both the panel and the widgets,
 * plus the simulation *job gate* (`useSimulationGate`): discovery of the
 * newest simulate job for a log and a mutation to start one. The gate is what
 * lets a dashboard widget run a simulation directly and show "running" for a
 * run started anywhere (panel, another tab) – the per-user `/api/v1/jobs`
 * list is the shared source of truth, because the panel and the dashboard
 * render in different QueryClients and can't see each other's cache.
 */

import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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
  /** One entry per simulated run; absent on caches written before per-run
   * downloads (the panel then falls back to a single run-0 button). */
  downloads?: { index: number; cases?: number; events?: number }[];
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

export const resultsKey = (logId: string) =>
  ["modules", "agentsimulator", "results", logId] as const;

export function useAgentSimResults(logId: string) {
  return useQuery<AgentSimResult>({
    queryKey: resultsKey(logId),
    queryFn: () =>
      api(`/api/v1/modules/agentsimulator/results?log_id=${encodeURIComponent(logId)}`, {
        headers: CANONICAL_RESULT_HEADERS,
      }),
    enabled: Boolean(logId),
    staleTime: 30_000,
  });
}

// ── simulation job gate ─────────────────────────────────────────────────────

/** Job type the platform registers for `@route.post("/simulate")` + `@job`
 * (`module.{module_id}.{path}` – see `loader._bind_route`). */
export const SIMULATE_JOB_TYPE = "module.agentsimulator.simulate";

/** Subset of the platform's `JobDetail` the gate reads. Progress fields are
 * persisted to the job row on a throttle, so polling shows a live fraction. */
export interface SimJob {
  id: string;
  status: string;
  progress_current: number;
  progress_total: number | null;
  stage: string | null;
  message: string | null;
  error: string | null;
  payload_json?: { log_id?: string };
  created_at: string;
}

const ACTIVE_STATUSES = new Set(["queued", "running", "paused"]);

export const isSimJobActive = (job: SimJob | null | undefined): boolean =>
  Boolean(job && ACTIVE_STATUSES.has(job.status));

export const simJobKey = (logId: string) =>
  ["modules", "agentsimulator", "sim-job", logId] as const;

/**
 * Newest simulate job for this log (any status) + a way to start one.
 *
 * - Discovery is a poll of the per-user jobs list filtered client-side by
 *   `payload_json.log_id`: 2.5s while a job is active (live-ish progress),
 *   a slow 15s otherwise so a run started on another surface still shows up.
 *   Widgets pass `watch: false` once results exist – then nothing polls.
 * - `start()` POSTs the module's `/simulate` route. No config PUT: the job
 *   reads `ctx.config`, which falls back to the stored per-user config or the
 *   manifest defaults – exactly what a dashboard-triggered run should use.
 *   The canonical-filter header pins the run to the unfiltered cache
 *   namespace (see `CANONICAL_RESULT_HEADERS` above).
 * - When a *watched* job is seen going active → terminal, the results query is
 *   invalidated once, so charts appear (or the prompt returns) without a
 *   manual refresh.
 */
export function useSimulationGate(logId: string, opts: { watch?: boolean } = {}) {
  const qc = useQueryClient();
  const watch = opts.watch ?? true;

  const jobQuery = useQuery<SimJob | null>({
    queryKey: simJobKey(logId),
    queryFn: async () => {
      const jobs = await api<SimJob[]>(`/api/v1/jobs?type=${SIMULATE_JOB_TYPE}&limit=100`);
      return jobs.find((j) => j.payload_json?.log_id === logId) ?? null;
    },
    enabled: Boolean(logId) && watch,
    refetchInterval: (query) => (isSimJobActive(query.state.data) ? 2_500 : 15_000),
  });

  const job = jobQuery.data ?? null;
  const activeJob = job != null && isSimJobActive(job) ? job : null;

  // Observed active → terminal transition ⇒ refetch results once. Guarded by
  // the previous (id, active) pair so a widget mounting onto an already
  // finished job doesn't re-invalidate what it just fetched.
  const lastSeen = useRef<{ id: string; active: boolean } | null>(null);
  useEffect(() => {
    if (!job) return;
    const prev = lastSeen.current;
    const nowActive = isSimJobActive(job);
    lastSeen.current = { id: job.id, active: nowActive };
    if (prev && prev.id === job.id && prev.active && !nowActive) {
      void qc.invalidateQueries({ queryKey: resultsKey(logId) });
    }
  }, [job, logId, qc]);

  const start = useMutation({
    mutationFn: async () => {
      const { job_id } = await api<{ job_id: string }>(
        `/api/v1/modules/agentsimulator/simulate?log_id=${encodeURIComponent(logId)}`,
        { method: "POST", headers: CANONICAL_RESULT_HEADERS },
      );
      return job_id;
    },
    onSuccess: (jobId) => {
      // Paint "running" immediately; the poll takes over on its next tick.
      qc.setQueryData<SimJob | null>(simJobKey(logId), {
        id: jobId,
        status: "queued",
        progress_current: 0,
        progress_total: null,
        stage: null,
        message: null,
        error: null,
        payload_json: { log_id: logId },
        created_at: new Date().toISOString(),
      });
      void qc.invalidateQueries({ queryKey: simJobKey(logId) });
    },
  });

  return {
    /** Newest simulate job for this log, any status; null = never run (or not visible). */
    job,
    /** The job while queued/running/paused – drives the "Simulation running…" state. */
    activeJob,
    /** True while the first jobs-list fetch is in flight (watch mode only). */
    checking: watch && jobQuery.isLoading,
    start: () => start.mutate(),
    startAsync: () => start.mutateAsync(),
    starting: start.isPending,
    startError: start.error,
  };
}
