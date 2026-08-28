"use client";

import type { ActivityDetail } from "@/lib/api-types";
import { formatNumber, formatRelative } from "@/lib/format";

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

export function ActivityHeader({ detail }: { detail: ActivityDetail }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-5">
      <Stat label="Events" value={formatNumber(detail.event_count)} />
      <Stat label="Event share" value={pct(detail.event_pct)} />
      <Stat label="Cases" value={formatNumber(detail.case_count)} />
      <Stat label="Case share" value={pct(detail.case_pct)} />
      <Stat
        label="Avg per case"
        value={
          detail.avg_occurrences_per_case === null
            ? "–"
            : `${detail.avg_occurrences_per_case.toFixed(1)}×`
        }
      />
      <Stat
        label="Max per case"
        value={
          detail.max_occurrences_per_case === null
            ? "–"
            : `${formatNumber(detail.max_occurrences_per_case)}×`
        }
      />
      <Stat
        label="Starts case"
        value={`${formatNumber(detail.start_case_count)} · ${pct(detail.start_case_pct)}`}
      />
      <Stat
        label="Ends case"
        value={`${formatNumber(detail.end_case_count)} · ${pct(detail.end_case_pct)}`}
      />
      <Stat label="First seen" value={formatRelative(detail.first_seen)} />
      <Stat label="Last seen" value={formatRelative(detail.last_seen)} />
    </dl>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">{label}</dt>
      <dd className="text-sm font-semibold tabular-nums text-foreground">{value}</dd>
    </div>
  );
}
