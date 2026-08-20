"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatDuration } from "@/lib/format";

import { usePerformanceBottlenecks } from "../panel/queries";
import { CardShell } from "./_kit";

/** Top time-consuming activities, ranked by average sojourn time. */
export default function Bottlenecks({
  logId,
  config,
}: {
  logId: string;
  config?: Record<string, unknown>;
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
      empty={isError || items.length === 0}
      emptyText="No bottlenecks detected."
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart layout="vertical" data={items} margin={{ left: 8, right: 16, top: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" horizontal={false} />
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
              key === "avg" ? [formatDuration(v), "Avg sojourn"] : [v, key]
            }
            contentStyle={{ fontSize: 12 }}
          />
          <Bar dataKey="avg" radius={[0, 3, 3, 0]}>
            {items.map((_, i) => (
              <Cell key={i} className="fill-primary" />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </CardShell>
  );
}
