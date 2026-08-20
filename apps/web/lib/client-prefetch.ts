"use client";

/**
 * Intent-prefetch helpers — warm the browser QueryClient cache for the data a
 * navigation is *about* to need (sidebar hover → that section's list; table-row
 * hover → that item's detail). They use the exact same `queryKey` + path the
 * destination hook uses (parity via `lib/query-keys.ts`), so when the page
 * mounts its `useQuery` reads a warm, fresh cache and renders without a skeleton.
 *
 * `prefetchQuery` is a no-op when the data is already fresh (within staleTime),
 * so spamming hover is cheap. Call from `onMouseEnter`/`onFocus` handlers with
 * the client from `useQueryClient()`.
 */

import type { QueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import {
  queryKeys,
  dashboardKeys,
  eventLogsListPath,
  eventLogPath,
  dashboardsListPath,
  dashboardPath,
  modulesListPath,
} from "@/lib/query-keys";
import type { EventLogDetail, EventLogSummary, ModuleSummary } from "@/lib/api-types";
import type { DashboardDetail, DashboardSummary } from "@/lib/dashboard-queries";

const STALE = 60_000;

export function prefetchEventLogs(
  qc: QueryClient,
  params: { q?: string; status?: string } = {},
): void {
  void qc.prefetchQuery({
    queryKey: [...queryKeys.eventLogs(), params],
    queryFn: () => api<EventLogSummary[]>(eventLogsListPath(params)),
    staleTime: STALE,
  });
}

export function prefetchEventLog(qc: QueryClient, id: string): void {
  void qc.prefetchQuery({
    queryKey: queryKeys.eventLog(id),
    queryFn: () => api<EventLogDetail>(eventLogPath(id)),
    staleTime: STALE,
  });
}

export function prefetchDashboards(qc: QueryClient): void {
  void qc.prefetchQuery({
    queryKey: dashboardKeys.all(),
    queryFn: () => api<DashboardSummary[]>(dashboardsListPath()),
    staleTime: STALE,
  });
}

export function prefetchDashboard(qc: QueryClient, id: string): void {
  void qc.prefetchQuery({
    queryKey: dashboardKeys.detail(id),
    queryFn: () => api<DashboardDetail>(dashboardPath(id)),
    staleTime: STALE,
  });
}

export function prefetchModules(qc: QueryClient): void {
  void qc.prefetchQuery({
    queryKey: queryKeys.modules(null),
    queryFn: () => api<ModuleSummary[]>(modulesListPath(null)),
    staleTime: STALE,
  });
}
