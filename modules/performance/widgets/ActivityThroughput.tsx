"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { CardShell, CHART_CHROME, seriesColor } from "@/components/dashboards/kit";
import { formatNumber } from "@/lib/format";
import type { DrillHandler } from "@/lib/dashboards/drill";

import { usePerformanceKpis } from "../panel/queries";

/** Event frequency per activity – the busiest steps in the process. */
export default function ActivityThroughput({
  logId,
  config,
  onDrill,
}: {
  logId: string;
  config?: Record<string, unknown>;
  onDrill?: DrillHandler;
}) {
  const { data, isLoading, isError } = usePerformanceKpis(logId);
  const topN = typeof config?.top_n === "number" ? config.top_n : 10;
  const rows = [...(data?.per_activity ?? [])]
    .sort((a, b) => b.frequency - a.frequency)
    .slice(0, topN)
    .map((a) => ({ activity: a.activity, frequency: a.frequency }));

  return (
    <CardShell loading={isLoading} error={isError} empty={rows.length === 0}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ left: 0, right: 8, top: 4, bottom: 28 }}>
          {/* Solid hairline, not dashed — dashed reads as a threshold. */}
          <CartesianGrid stroke={CHART_CHROME.grid} vertical={false} />
          <XAxis
            dataKey="activity"
            angle={-35}
            textAnchor="end"
            interval={0}
            height={40}
            tick={{ fontSize: 9 }}
            stroke="currentColor"
            className="text-muted-foreground"
          />
          <YAxis
            tickFormatter={(v) => formatNumber(v)}
            tick={{ fontSize: 10 }}
            width={36}
            stroke="currentColor"
            className="text-muted-foreground"
          />
          <Tooltip formatter={(v: number) => [formatNumber(v), "Events"]} contentStyle={{ fontSize: 12 }} />
          {/* One series, one colour: shading bars by their own height would
              double-encode the value the bar length already carries. */}
          <Bar
            dataKey="frequency"
            fill={seriesColor(0)}
            radius={[3, 3, 0, 0]}
            cursor={onDrill ? "pointer" : undefined}
            onClick={(bar: { activity?: string }) =>
              bar?.activity && onDrill?.({ params: { activity: bar.activity } })
            }
          />
        </BarChart>
      </ResponsiveContainer>
    </CardShell>
  );
}
