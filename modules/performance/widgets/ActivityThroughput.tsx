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

import { formatNumber } from "@/lib/format";

import { usePerformanceKpis } from "../panel/queries";
import { CardShell } from "./_kit";

/** Event frequency per activity – the busiest steps in the process. */
export default function ActivityThroughput({
  logId,
  config,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const { data, isLoading, isError } = usePerformanceKpis(logId);
  const topN = typeof config?.top_n === "number" ? config.top_n : 10;
  const rows = [...(data?.per_activity ?? [])]
    .sort((a, b) => b.frequency - a.frequency)
    .slice(0, topN)
    .map((a) => ({ activity: a.activity, frequency: a.frequency }));

  return (
    <CardShell loading={isLoading} empty={isError || rows.length === 0}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ left: 0, right: 8, top: 4, bottom: 28 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" vertical={false} />
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
          <Bar dataKey="frequency" className="fill-primary" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </CardShell>
  );
}
