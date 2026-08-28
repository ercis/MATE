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

import { CONF_COLORS } from "../panel/conformance-decorate";
import { useConformanceResultsAuto } from "../panel/queries";

/** Activities with the most conformance deviations. */
export default function TopDeviations({
  logId,
  config,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const { data, isLoading, isError } = useConformanceResultsAuto(logId);
  const topN = typeof config?.top_n === "number" ? config.top_n : 10;
  const rows = (data?.per_activity ?? [])
    .filter((a) => a.deviations > 0)
    .slice(0, topN)
    .map((a) => ({ activity: a.activity, deviations: a.deviations }));

  return (
    <CardShell
      loading={isLoading}
      empty={isError || !data?.ran || rows.length === 0}
      emptyText={data?.ran ? "No deviations – the log fits the model." : "Run a conformance check first."}
    >
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
          <Tooltip
            formatter={(v: number) => [formatNumber(v), "Deviations"]}
            contentStyle={{ fontSize: 12 }}
          />
          {/* Inline fill: `fill-red-500/80` is not compiled for module sources. */}
          <Bar dataKey="deviations" fill={CONF_COLORS.chart} fillOpacity={0.8} radius={[0, 3, 3, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </CardShell>
  );
}
