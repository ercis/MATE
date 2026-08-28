"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { setLogScopedFilter } from "@/lib/api";
import { encodeFilterHeader } from "@/components/dashboards/widget-filter";
import { DashboardFilterBar } from "@/components/dashboards/dashboard-filter-bar";
import { DashboardTimeRange } from "@/components/dashboards/dashboard-time-range";
import { useEventColumns, useTimeBounds } from "@/lib/dashboard-queries";
import { useLogFilter, useLogFilterStore } from "@/lib/stores/log-filter";

/**
 * Log-scoped filter shell for a single log's MODULE PANEL views.
 *
 * Renders a reused column-filter bar + time-range control above the module
 * panel and re-scopes EVERY module view (discovery, performance, complexity, …)
 * for the log by pushing the filter onto the `X-FF-Event-Filter` request header
 * (via `setLogScopedFilter`, applied gated & additive in `lib/api.ts`). Filter
 * state lives in `useLogFilterStore` keyed by `logId`, so it survives
 * navigation between this log's module pages.
 *
 * Panel queries run in a DEDICATED QueryClient so (1) a filter change can
 * `resetQueries()` them all to skeleton + refetch under the new header, and (2)
 * filtered results never contaminate the shared app cache under the module
 * queries' filter-agnostic keys. Mirrors the dashboard's
 * DashboardFilterProvider / DashboardWidgetScope mechanism.
 *
 * Boundary: this is the module-panel surface ONLY. Dashboards have their own
 * board-global + per-widget filter (they own the ambient `X-FF-Event-Filter`);
 * rule (c) in `applyAmbientHeaders` guarantees this never overrides them, and
 * the cleanup below clears the active-log filter on unmount so it can't leak
 * onto other surfaces (dashboards included).
 */
export function LogFilterProvider({
  logId,
  enabled = true,
  children,
}: {
  logId: string;
  /** False for modules that opt out (`frontend.log_filter: false`) or ship no
   * panel. Hides the bar AND holds the header at null, so an opted-out panel is
   * never narrowed by a filter the user can't see (the log's stored filter
   * survives in the store for the modules that do show it). The panel still
   * gets its own QueryClient, so cache isolation is uniform across module
   * pages. */
  enabled?: boolean;
  children: ReactNode;
}) {
  const filter = useLogFilter(logId);
  const setColumnFilters = useLogFilterStore((s) => s.setColumnFilters);
  const setTimeFilters = useLogFilterStore((s) => s.setTimeFilters);

  // Metadata for the bar + slider. Fetched here (OUTSIDE the panel's dedicated
  // client) so a filter commit's resetQueries never churns them. Passing null
  // when disabled skips both fetches – nothing renders them.
  const { data: columns } = useEventColumns(enabled ? logId : null);
  const { data: bounds } = useTimeBounds(enabled ? logId : null);

  const [panelClient] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: 1 } } }),
  );

  // Time entries first, then columns — same order the dashboard serializes.
  // Held empty when disabled so the effect below pushes a null header: the
  // log's stored filter stays in the store (a filter-capable module restores
  // it) but never rides onto an opted-out module's requests.
  const combined = useMemo(
    () => (enabled ? [...filter.timeFilters, ...filter.columnFilters] : []),
    [enabled, filter.timeFilters, filter.columnFilters],
  );

  // Push the active log's filter onto the ambient header (read lazily at fetch
  // time by applyAmbientHeaders) and refetch every panel query on change. Skip
  // the reset on the first run: nothing is cached yet and the panel's own
  // initial fetch already reads the header, so a fresh mount that restores a
  // stored filter applies it without a redundant double-fetch.
  const firstRun = useRef(true);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const header = combined.length > 0 ? encodeFilterHeader(combined) : null;
    if (firstRun.current) {
      firstRun.current = false;
      setLogScopedFilter(logId, header);
      return;
    }
    // Debounce: typing in the bar / dragging the slider churns `combined`.
    // Coalescing into one update per ~300ms idle means the panel recomputes
    // once instead of on every keystroke/tick.
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      setLogScopedFilter(logId, header);
      void panelClient.resetQueries();
    }, 300);
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [combined, logId, panelClient]);

  // Clear the active-log filter when leaving this log's module surface so the
  // header can never ride onto another page's (or a dashboard's) requests.
  useEffect(() => () => setLogScopedFilter(null, null), []);

  const hasColumns = enabled && !!columns && columns.length > 0;
  const hasBounds = enabled && !!bounds?.field && !!bounds.min_ts && !!bounds.max_ts;

  // No heading: the bar sits directly above the panel it scopes, and the
  // module page is the only thing it can mean. `compact` keeps it to a thin
  // strip so the panel — the actual content — keeps the vertical space.
  return (
    <div className="flex flex-col gap-3">
      {(hasColumns || hasBounds) && (
        <div className="overflow-hidden rounded-xl border border-border">
          {hasColumns && (
            <DashboardFilterBar
              compact
              logId={logId}
              columns={columns!}
              filters={filter.columnFilters}
              onChange={(next) => setColumnFilters(logId, next)}
            />
          )}
          {hasBounds && (
            <DashboardTimeRange
              compact
              bounds={bounds}
              committed={filter.timeFilters}
              onChange={(next) => setTimeFilters(logId, next)}
            />
          )}
        </div>
      )}
      <QueryClientProvider client={panelClient}>{children}</QueryClientProvider>
    </div>
  );
}
