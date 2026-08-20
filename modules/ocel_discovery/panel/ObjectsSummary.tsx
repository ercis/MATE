"use client";

import { formatDuration, formatNumber } from "@/lib/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import type { ObjectsSummaryData, ObjectTypeLifecycle } from "./queries";

/** Per-object-type lifecycle stats – reused by the panel tab and the dashboard
 *  widget. Reproduces the pm4py `ocel_objects_summary` aggregates. */
export function ObjectTypeStats({
  types,
  hasInteracting,
  compact = false,
}: {
  types: ObjectTypeLifecycle[];
  hasInteracting: boolean;
  compact?: boolean;
}) {
  if (types.length === 0)
    return <p className="py-6 text-center text-xs text-muted-foreground">No objects in this log.</p>;

  return (
    <div className={compact ? "space-y-1.5" : "space-y-2"}>
      {types.map((t) => (
        <div
          key={t.type}
          className="rounded-md border border-border/60 bg-muted/20 px-3 py-2"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-sm font-medium">{t.type}</span>
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
              {formatNumber(t.objects)} objects
            </span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-muted-foreground">
            <Stat label="median lifecycle" value={formatDuration(t.median_duration_s)} />
            {!compact && <Stat label="avg lifecycle" value={formatDuration(t.avg_duration_s)} />}
            <Stat
              label="avg events"
              value={t.avg_events != null ? formatNumber(Math.round(t.avg_events * 10) / 10) : "–"}
            />
            {hasInteracting && (
              <Stat
                label="avg interacting"
                value={
                  t.avg_interacting != null
                    ? formatNumber(Math.round(t.avg_interacting * 10) / 10)
                    : "–"
                }
              />
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="uppercase tracking-wide">{label}</span>
      <span className="font-medium tabular-nums text-foreground">{value}</span>
    </span>
  );
}

/** Full lifecycle summary: per-type stats plus the longest-lived objects. */
export function ObjectsSummary({ data }: { data: ObjectsSummaryData }) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div className="space-y-2">
        <h3 className="text-sm font-medium">Object types</h3>
        <ObjectTypeStats types={data.types} hasInteracting={data.has_interacting} />
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium">Longest-lived objects</h3>
        {data.top_objects.length === 0 ? (
          <p className="py-6 text-center text-xs text-muted-foreground">No objects to rank.</p>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Object</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead className="text-right">Lifecycle</TableHead>
                  <TableHead className="text-right">Events</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.top_objects.map((o) => (
                  <TableRow key={o.oid}>
                    <TableCell className="max-w-[160px] truncate font-mono text-xs" title={o.oid}>
                      {o.oid}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{o.type}</TableCell>
                    <TableCell className="text-right text-xs tabular-nums">
                      {formatDuration(o.duration_s)}
                    </TableCell>
                    <TableCell className="text-right text-xs tabular-nums">
                      {formatNumber(o.n_events)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  );
}
