"use client";

import { create } from "zustand";
import type { FilterEntry } from "@/lib/api-types";

/**
 * Per-log ephemeral event filter for the MODULE-PANEL surface.
 *
 * Mirrors the dashboard filter's two channels (column filters + a time-range
 * window) but is scoped to a single event log instead of a dashboard board. A
 * filter set here re-scopes every module view (discovery, performance,
 * complexity, …) opened for that log: it rides the same base64
 * `X-FF-Event-Filter` request header the dashboard uses (encoded via the shared
 * `encodeFilterHeader` serializer) and is attached — gated & additive — by
 * `applyAmbientHeaders` in `lib/api.ts`.
 *
 * State is keyed by `logId` so moving between logs keeps each log's own filter.
 * It is ephemeral (in-memory only): a reload resets to no filter. This is NOT
 * the log's committed Events-tab filter (`EventLog.active_filter`) — it never
 * mutates server state.
 */
export interface LogFilter {
  columnFilters: FilterEntry[];
  timeFilters: FilterEntry[];
}

/** Stable empty default so `useLogFilter` returns a referentially-constant value
 * for an unset log (no spurious re-renders). */
export const EMPTY_LOG_FILTER: LogFilter = { columnFilters: [], timeFilters: [] };

interface LogFilterState {
  byLog: Record<string, LogFilter>;
  setColumnFilters: (logId: string, next: FilterEntry[]) => void;
  setTimeFilters: (logId: string, next: FilterEntry[]) => void;
  clear: (logId: string) => void;
}

export const useLogFilterStore = create<LogFilterState>((set) => ({
  byLog: {},
  setColumnFilters: (logId, next) =>
    set((s) => ({
      byLog: {
        ...s.byLog,
        [logId]: { ...(s.byLog[logId] ?? EMPTY_LOG_FILTER), columnFilters: next },
      },
    })),
  setTimeFilters: (logId, next) =>
    set((s) => ({
      byLog: {
        ...s.byLog,
        [logId]: { ...(s.byLog[logId] ?? EMPTY_LOG_FILTER), timeFilters: next },
      },
    })),
  clear: (logId) =>
    set((s) => {
      if (!(logId in s.byLog)) return s;
      const { [logId]: _omit, ...rest } = s.byLog;
      return { byLog: rest };
    }),
}));

/** Read a single log's filter (stable empty default when unset). */
export function useLogFilter(logId: string): LogFilter {
  return useLogFilterStore((s) => s.byLog[logId] ?? EMPTY_LOG_FILTER);
}
