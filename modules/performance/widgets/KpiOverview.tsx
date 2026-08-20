"use client";

import { formatDuration, formatNumber } from "@/lib/format";

import { usePerformanceKpis } from "../panel/queries";
import { CardShell, KpiGrid, KpiTile } from "./_kit";

export default function KpiOverview({ logId }: { logId: string }) {
  const { data, isLoading, isError } = usePerformanceKpis(logId);
  const s = data?.summary;
  return (
    <CardShell loading={isLoading} empty={isError || !s}>
      {s && (
        <KpiGrid>
          <KpiTile label="Cases" value={formatNumber(s.cases)} />
          <KpiTile label="Events" value={formatNumber(s.events)} />
          <KpiTile label="Variants" value={formatNumber(s.variants)} />
          <KpiTile label="Throughput" value={`${formatNumber(s.throughput_cases_per_day)}/day`} />
          <KpiTile label="Avg cycle" value={formatDuration(s.avg_cycle_time_s)} />
          <KpiTile label="Median cycle" value={formatDuration(s.median_cycle_time_s)} />
          <KpiTile label="P90 cycle" value={formatDuration(s.p90_cycle_time_s)} />
          <KpiTile label="Lead time" value={formatDuration(s.lead_time_s)} />
        </KpiGrid>
      )}
    </CardShell>
  );
}
