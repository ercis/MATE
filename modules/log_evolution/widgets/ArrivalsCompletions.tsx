"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useEvolution, type Granularity } from "../panel/queries";
import { ChartFrame, LegendDot } from "./_kit";

/**
 * Cases started vs. completed per period. When arrivals run above completions
 * the backlog is growing; when they cross the other way it's draining. Pick the
 * calendar period with `config.granularity`.
 */
export default function ArrivalsCompletions({
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
    arrivals: data?.arrivals[i] ?? 0,
    completions: data?.completions[i] ?? 0,
  }));

  return (
    <ChartFrame
      loading={isLoading}
      empty={isError || points.length === 0}
      emptyText="No time series for this log yet."
      legend={
        <>
          <LegendDot color="var(--chart-1)" label="Arrivals" />
          <LegendDot color="var(--chart-2)" label="Completions" />
        </>
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ left: 0, right: 8, top: 4, bottom: 4 }}>
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
          <Line
            type="monotone"
            dataKey="arrivals"
            name="Arrivals"
            stroke="var(--chart-1)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="completions"
            name="Completions"
            stroke="var(--chart-2)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
