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
import { formatDuration } from "@/lib/format";
import type { DrillHandler } from "@/lib/dashboards/drill";

import { usePerformanceBottlenecks } from "../panel/queries";

/** Top time-consuming activities, ranked by average time spent (sojourn time). */
export default function Bottlenecks({
  logId,
  config,
  onDrill,
}: {
  logId: string;
  config?: Record<string, unknown>;
  onDrill?: DrillHandler;
}) {
  const { data, isLoading, isError } = usePerformanceBottlenecks(logId);
  const topN = typeof config?.top_n === "number" ? config.top_n : 8;
  const items = (data?.items ?? []).slice(0, topN).map((b) => ({
    activity: b.activity,
    avg: b.avg_sojourn_s,
    share: b.share_of_total_time,
  }));

  return (
    <CardShell
      loading={isLoading}
      error={isError}
      empty={items.length === 0}
      emptyText="No bottlenecks detected."
      errorText="Could not load bottlenecks."
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart layout="vertical" data={items} margin={{ left: 8, right: 16, top: 4, bottom: 4 }}>
          {/* Solid hairline, not dashed: a dashed rule reads as a threshold or
              a projection, and this is only a grid. */}
          <CartesianGrid stroke={CHART_CHROME.grid} horizontal={false} />
          <XAxis
            type="number"
            tickFormatter={(v) => formatDuration(v)}
            tick={{ fontSize: 10 }}
            stroke="currentColor"
            className="text-muted-foreground"
          />
          <YAxis
            type="category"
            dataKey="activity"
            width={110}
            tick={{ fontSize: 10 }}
            stroke="currentColor"
            className="text-muted-foreground"
          />
          <Tooltip
            formatter={(v: number, key) =>
              key === "avg" ? [formatDuration(v), "Avg time spent"] : [v, key]
            }
            contentStyle={{ fontSize: 12 }}
          />
          {/* One series, one colour. Shading each bar by its own length would
              double-encode the value as hue and spend the only free channel on
              information the bar length already carries. */}
          <Bar
            dataKey="avg"
            radius={[0, 3, 3, 0]}
            fill={seriesColor(0)}
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
