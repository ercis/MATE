"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  ActivityDeltasData,
  BpmnDiffData,
  DfgDiffData,
  LogFilterDetail,
  LogSummary,
  Side,
  SimilarityData,
  SummaryDeltaData,
  VariantDiffData,
} from "./types";

const STALE_TIME = 30_000;
const MOD = "/api/v1/modules/process_comparison";

/**
 * Serialise the compared sides for the `sides` query param: base64 of a JSON
 * list of `{log, filter}`. Same UTF-8 → binary → btoa dance the platform's
 * filter header uses, because `btoa` throws on non-Latin1 and filter values
 * carry real activity/attribute text.
 */
export function encodeSides(sides: Side[]): string {
  const json = JSON.stringify(sides.map((s) => ({ log: s.log, filter: s.filter })));
  const bytes = new TextEncoder().encode(json);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

/** React-Query key fragment: a side is identified by its log AND its filter, so
 *  the same log under two filters can't collide on one cache entry. */
function sidesKey(sides: Side[]): string[] {
  return sides.map((s) => `${s.log}|${JSON.stringify(s.filter)}`);
}

/** Two sides that are both picked and not literally the same (log + filter) —
 *  the backend refuses that pair, so don't fire the request at all. */
export function sidesReady(sides: Side[]): boolean {
  if (sides.length < 2 || sides.some((s) => !s.log)) return false;
  const keys = sidesKey(sides);
  return new Set(keys).size === keys.length;
}

/** `log_id` still rides along even though every side names its own log: it is
 *  the platform's route scope, and what binds `ctx.cache` to a log directory.
 *  Without it the context is unbound and the first `cache.set` raises "This
 *  handler isn't scoped to a log_id". It stays the log the PANEL was opened on
 *  - that is where the comparison's results (and the AI guidance that reads
 *  them) belong, whichever logs the sides point at. */
function url(path: string, logId: string, sides: Side[]): string {
  const search = new URLSearchParams({ log_id: logId, sides: encodeSides(sides) });
  return `${MOD}${path}?${search.toString()}`;
}

/** Ready case-centric logs the user can compare (excludes OCEL). Includes the
 *  log the panel was opened on: either side may point at any of them. */
export function useComparisonLogs() {
  return useQuery<LogSummary[]>({
    queryKey: ["modules", "process_comparison", "logs"],
    queryFn: () => api<LogSummary[]>("/api/v1/event-logs?status=ready"),
    staleTime: STALE_TIME,
    select: (logs) => logs.filter((l) => l.log_model === "case_centric" && l.status === "ready"),
  });
}

/** A log's committed Events-tab filter — seeds a side when its log is picked so
 *  the panel shows (and lets the user edit) the filter that would otherwise
 *  apply invisibly. */
export function useLogFilterDetail(logId: string | null) {
  return useQuery<LogFilterDetail>({
    queryKey: ["modules", "process_comparison", "log-detail", logId],
    queryFn: () => api<LogFilterDetail>(`/api/v1/event-logs/${logId}`),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

function useSidesQuery<T>(view: string, path: string, logId: string, sides: Side[]) {
  return useQuery<T>({
    queryKey: ["modules", "process_comparison", view, logId, sidesKey(sides)],
    queryFn: () => api<T>(url(path, logId, sides)),
    enabled: Boolean(logId) && sidesReady(sides),
    staleTime: STALE_TIME,
  });
}

export function useSimilarity(logId: string, sides: Side[]) {
  return useSidesQuery<SimilarityData>("similarity", "/similarity", logId, sides);
}

export function useDfgOverlay(logId: string, sides: Side[]) {
  return useSidesQuery<DfgDiffData>("dfg-overlay", "/dfg-overlay", logId, sides);
}

export function useSummaryDelta(logId: string, sides: Side[]) {
  return useSidesQuery<SummaryDeltaData>("summary", "/summary", logId, sides);
}

export function useBpmnDiff(logId: string, sides: Side[]) {
  return useSidesQuery<BpmnDiffData>("bpmn", "/bpmn", logId, sides);
}

export function useVariantDiff(logId: string, sides: Side[]) {
  return useSidesQuery<VariantDiffData>("variants", "/variants", logId, sides);
}

export function useActivityDeltas(logId: string, sides: Side[]) {
  return useSidesQuery<ActivityDeltasData>("activity-deltas", "/activity-deltas", logId, sides);
}
