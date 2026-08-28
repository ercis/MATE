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

import {CardShell} from "@/components/dashboards/kit";
import { formatNumber } from "@/lib/format";

import { useDiscoveryDfg } from "../panel/queries";

/** Most frequent activities in the discovered process. */
export default function ActivityFrequency({
  logId,
  config,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const { data, isLoading, isError } = useDiscoveryDfg(logId);
  const topN = typeof config?.top_n === "number" ? config.top_n : 10;
  const rows = [...(data?.activities ?? [])]
    .sort((a, b) => b.frequency - a.frequency)
    .slice(0, topN)
    .map((a) => ({ activity: a.label, frequency: a.frequency }));

  return (
    <CardShell loading={isLoading} empty={isError || rows.length === 0}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart layout="vertical" data={rows} margin={{ left: 8, right: 16, top: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" horizontal={false} />
          <XAxis
            type="number"
            tickFormatter={(v) => formatNumber(v)}
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
          <Tooltip formatter={(v: number) => [formatNumber(v), "Frequency"]} contentStyle={{ fontSize: 12 }} />
          <Bar dataKey="frequency" className="fill-primary" radius={[0, 3, 3, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </CardShell>
  );
}
