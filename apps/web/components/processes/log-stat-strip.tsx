"use client";

import { FormatBadge } from "@/components/processes/format-badge";
import { formatDateRange, formatNumber, formatRelative } from "@/lib/format";
import type { EventLogDetail } from "@/lib/api-types";

/**
 * The shape of a case-centric log, above its module grid.
 *
 * OCEL logs get `OcelOverviewPanel`; case-centric logs had no equivalent, so the
 * only numbers on a process page were the tab badges. These are the same fields
 * the processes list already shows, restated where you actually read them - the
 * sanity check before trusting any module's output.
 *
 * Rendered for every status (not gated on `ready`) so its `data-tour` anchor
 * always exists. The counts are written in the same UPDATE that flips a log to
 * `processing`, so they are populated well before the modules finish.
 */
export function LogStatStrip({ log }: { log: EventLogDetail }) {
  return (
    <dl
      data-tour="log-stats"
      className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-lg border bg-card p-4 text-sm sm:grid-cols-3 lg:grid-cols-6"
    >
      <Stat label="Cases" value={formatNumber(log.cases_count)} />
      <Stat label="Events" value={formatNumber(log.events_count)} />
      <Stat label="Variants" value={formatNumber(log.variants_count)} />
      <Stat label="Date range" value={formatDateRange(log.date_min, log.date_max)} />
      <Stat label="Format" value={<FormatBadge format={log.source_format} />} />
      <Stat label="Imported" value={formatRelative(log.imported_at ?? log.created_at)} />
    </dl>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0 space-y-0.5">
      <dt className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
        {label}
      </dt>
      <dd className="truncate text-sm font-semibold tabular-nums text-foreground">{value}</dd>
    </div>
  );
}
