"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useEvolution, type Granularity } from "../panel/queries";
import { ChartFrame } from "./_kit";

/**
 * Work-in-progress: open cases (started but not yet finished) at the end of each
 * period. Sustained peaks flag periods where the process carried a heavy backlog.
 */
export default function ActiveCases({
  logId,
  config,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const granularity = (typeof config?.granularity === "string"
    ? config.granularity
    : "auto") as Granularity;
  const { data, isLoading, isError } = useEvolution(logId, granularity);

  const points = (data?.periods ?? []).map((label, i) => ({
    label,
    active: data?.active[i] ?? 0,
  }));

  return (
    <ChartFrame
      loading={isLoading}
      empty={isError || points.length === 0}
      emptyText="No time series for this log yet."
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ left: 0, right: 8, top: 4, bottom: 4 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 9, fill: "var(--muted-foreground)" }}
            interval="preserveStartEnd"
            minTickGap={24}
            stroke="var(--border)"
          />
          <YAxis
            tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
            width={36}
            stroke="var(--border)"
            allowDecimals={false}
          />
          <Tooltip contentStyle={{ fontSize: 12 }} />
          <Area
            type="monotone"
            dataKey="active"
            name="Active cases"
            stroke="var(--chart-1)"
            strokeWidth={2}
            fill="var(--chart-1)"
            fillOpacity={0.2}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
