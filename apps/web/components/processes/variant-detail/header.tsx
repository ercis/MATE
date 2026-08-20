"use client";

import type { VariantDetail } from "@/lib/api-types";
import { formatDuration, formatNumber, formatRelative } from "@/lib/format";

export function VariantHeader({ variant }: { variant: VariantDetail }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-6">
      <Stat label="Cases" value={formatNumber(variant.case_count)} />
      <Stat label="Share" value={`${(variant.case_pct * 100).toFixed(1)}%`} />
      <Stat label="Avg duration" value={formatDuration(variant.avg_duration_seconds)} />
      <Stat label="Median" value={formatDuration(variant.median_duration_seconds)} />
      <Stat label="P90" value={formatDuration(variant.p90_duration_seconds)} />
      <Stat label="Last seen" value={formatRelative(variant.last_seen)} />
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
