"use client";

import { useEffect, useMemo, useRef } from "react";
import { Filter, Layers } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DashboardFilterBar } from "@/components/dashboards/dashboard-filter-bar";
import { DashboardTimeRange } from "@/components/dashboards/dashboard-time-range";
import { useEventColumns, useTimeBounds } from "@/lib/dashboard-queries";
import { formatNumber } from "@/lib/format";
import type { FilterEntry } from "@/lib/api-types";

import { useLogFilterDetail } from "./queries";
import type { LogSummary, Side } from "./types";

/** One side's editable state. Column filters and the time window are kept apart
 *  because two different controls own them; they are concatenated (time first,
 *  matching the platform's own order) into the flat `Side.filter` that travels
 *  to the backend. */
export interface SideState {
  log: string;
  columnFilters: FilterEntry[];
  timeFilters: FilterEntry[];
}

export function emptySide(log = ""): SideState {
  return { log, columnFilters: [], timeFilters: [] };
}

export function toSide(s: SideState): Side {
  return { log: s.log, filter: [...s.timeFilters, ...s.columnFilters] };
}

export function filterCount(s: SideState): number {
  return s.columnFilters.length + (s.timeFilters.length > 0 ? 1 : 0);
}

/** Human label for a side: the log's name plus how narrowed it is. With both
 *  sides allowed to point at the same log, the log name alone is ambiguous —
 *  the filter is what tells the two cohorts apart. */
export function sideLabel(
  letter: string,
  s: SideState,
  logs: LogSummary[],
): string {
  const name = logs.find((l) => l.id === s.log)?.name ?? "—";
  const n = filterCount(s);
  return n > 0 ? `${letter} · ${name} (${n} filter${n > 1 ? "s" : ""})` : `${letter} · ${name}`;
}

/**
 * Log picker + independent filter editor for ONE side of the comparison.
 *
 * Reuses the platform's own column-filter bar and time-range slider (the same
 * controls the dashboard and the module-panel filter shell use), so the filter
 * vocabulary is identical everywhere. The picked log's committed Events-tab
 * filter seeds the side once — without that the panel would silently apply a
 * filter the user can't see, or silently drop one they had committed.
 */
export function SidePicker({
  letter,
  swatch,
  logs,
  value,
  onChange,
}: {
  letter: string;
  swatch: string;
  logs: LogSummary[];
  value: SideState;
  onChange: (next: SideState) => void;
}) {
  const logId = value.log || null;
  const { data: columns } = useEventColumns(logId);
  const boundsQ = useTimeBounds(logId);
  const detailQ = useLogFilterDetail(logId);

  // Seed once per picked log. Guarded by a ref rather than the filter contents
  // so clearing a seeded filter sticks instead of being re-seeded on the next
  // render.
  const seededFor = useRef<string | null>(null);
  useEffect(() => {
    const detail = detailQ.data;
    if (!logId || !detail || detail.id !== logId || boundsQ.isPending) return;
    if (seededFor.current === logId) return;
    seededFor.current = logId;
    const committed = detail.active_filter ?? [];
    if (committed.length === 0) return;
    const tsField = boundsQ.data?.field;
    const timeFilters = committed.filter(
      (f) => !!tsField && f.field === tsField && (f.op === "gte" || f.op === "lte"),
    );
    const columnFilters = committed.filter((f) => !timeFilters.includes(f));
    onChange({ log: logId, columnFilters, timeFilters });
  }, [logId, detailQ.data, boundsQ.isPending, boundsQ.data, onChange]);

  const picked = useMemo(() => logs.find((l) => l.id === value.log), [logs, value.log]);
  const hasBounds = !!boundsQ.data?.field && !!boundsQ.data.min_ts && !!boundsQ.data.max_ts;

  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-xl border bg-card p-3">
      <div className="flex items-center gap-2">
        <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: swatch }} />
        <span className="text-xs font-semibold">{letter}</span>
        <Select
          value={value.log}
          onValueChange={(next) => {
            // A new log invalidates the old log's filters (its columns differ).
            seededFor.current = null;
            onChange(emptySide(next));
          }}
        >
          <SelectTrigger className="h-8 min-w-0 flex-1 text-xs">
            <SelectValue placeholder="Pick a log" />
          </SelectTrigger>
          <SelectContent>
            {logs.map((l) => (
              <SelectItem key={l.id} value={l.id}>
                {l.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {typeof picked?.cases_count === "number" && (
          <Badge variant="secondary" className="shrink-0 gap-1 text-[10px] tabular-nums">
            <Layers className="h-3 w-3" />
            {formatNumber(picked.cases_count)} cases
          </Badge>
        )}
      </div>

      {!value.log ? (
        <p className="text-xs text-muted-foreground">
          Pick a log, then narrow it with filters to compare a specific cohort.
        </p>
      ) : !columns ? (
        <Skeleton className="h-8 w-full" />
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <DashboardFilterBar
            compact
            logId={value.log}
            columns={columns}
            filters={value.columnFilters}
            onChange={(next) => onChange({ ...value, columnFilters: next })}
          />
          {hasBounds && (
            <DashboardTimeRange
              compact
              // Re-mount per log so the slider re-seeds from that log's bounds
              // and this side's committed window instead of keeping the old one.
              key={value.log}
              bounds={boundsQ.data}
              committed={value.timeFilters}
              onChange={(next) => onChange({ ...value, timeFilters: next })}
            />
          )}
        </div>
      )}

      {filterCount(value) > 0 && (
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Filter className="h-3 w-3" />
          This side reads only the matching rows.
        </div>
      )}
    </div>
  );
}
