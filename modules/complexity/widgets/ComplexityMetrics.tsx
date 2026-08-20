"use client";

import { formatNumber } from "@/lib/format";

import { useComplexityMetrics } from "../panel/queries";
import { CardShell, KpiTile } from "./_kit";

const fmt = (n: number | null | undefined, digits = 2) =>
  n == null ? "–" : n.toLocaleString(undefined, { maximumFractionDigits: digits });

/** Headline EPA-based complexity measures for the log. */
export default function ComplexityMetrics({ logId }: { logId: string }) {
  const { data, isLoading, isError } = useComplexityMetrics(logId);
  const m = data?.basic;
  return (
    <CardShell loading={isLoading} empty={isError || !m}>
      {m && (
        <div className="grid grid-cols-2 gap-2">
          <KpiTile label="Magnitude" value={formatNumber(m.magnitude)} hint="events" />
          <KpiTile label="Variety" value={formatNumber(m.variety)} hint="distinct activities" />
          <KpiTile label="Distinct traces" value={`${fmt(m.distinct_traces_pct * 100, 1)}%`} />
          <KpiTile label="Avg trace len" value={fmt(m.trace_length_avg, 1)} />
          <KpiTile label="Variant entropy" value={fmt(m.normalized_variant_entropy)} hint="normalized" />
          <KpiTile
            label="Sequence entropy"
            value={fmt(m.normalized_sequence_entropy)}
            hint="normalized"
          />
          <KpiTile label="Lempel–Ziv" value={fmt(m.lempel_ziv)} />
          <KpiTile label="Pentland" value={fmt(m.pentland_process)} hint="process" />
        </div>
      )}
    </CardShell>
  );
}
