"use client";

import { useMemo } from "react";
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import { colorAt, formatDay, useDotted } from "../panel/queries";
import { ChartFrame, LegendDot } from "./_kit";

const fillFor = (name: string, i: number) =>
  name === "Other" ? "var(--muted-foreground)" : colorAt(i);

/**
 * The classic dotted chart: one dot per event, x = time, y = case (ordered by
 * start time), colour = activity. Reveals batching, arrival patterns and drift
 * at a glance. `config.max_points` caps the rendered events (down-sampled above).
 */
export default function DottedChart({
  logId,
  config,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const maxPoints = typeof config?.max_points === "number" ? config.max_points : 8000;
  const { data, isLoading, isError } = useDotted(logId, maxPoints);

  // One Scatter series per activity → native per-activity colour.
  const series = useMemo(() => {
    if (!data) return [] as { name: string; index: number; points: { t: number; y: number }[] }[];
    const buckets: { t: number; y: number }[][] = data.activities.map(() => []);
    for (const pt of data.points) {
      (buckets[pt.a] ??= []).push({ t: pt.t, y: pt.y });
    }
    return data.activities.map((name, index) => ({ name, index, points: buckets[index] ?? [] }));
  }, [data]);

  const yMax = data && data.n_cases > 0 ? data.n_cases - 1 : "dataMax";

  return (
    <ChartFrame
      loading={isLoading}
      empty={isError || !data || data.points.length === 0}
      emptyText="No events to chart for this log yet."
      legend={series.map((s) => (
        <LegendDot key={s.name} color={fillFor(s.name, s.index)} label={s.name} />
      ))}
      caption={
        data?.sampled
          ? `Showing ${data.points.length.toLocaleString()} of ${data.total_events.toLocaleString()} events (sampled).`
          : undefined
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ left: 0, right: 8, top: 4, bottom: 4 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis
            type="number"
            dataKey="t"
            name="Time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v: number) => formatDay(v)}
            tick={{ fontSize: 9, fill: "var(--muted-foreground)" }}
            stroke="var(--border)"
          />
          <YAxis
            type="number"
            dataKey="y"
            name="Case"
            reversed
            domain={[0, yMax]}
            width={36}
            tick={{ fontSize: 9, fill: "var(--muted-foreground)" }}
            stroke="var(--border)"
          />
          <ZAxis range={[6, 6]} />
          <Tooltip
            cursor={{ strokeDasharray: "3 3" }}
            contentStyle={{ fontSize: 12 }}
            labelFormatter={() => ""}
            formatter={(value, name) =>
              name === "Time"
                ? [formatDay(Number(value)), "Time"]
                : [String(value), String(name)]
            }
          />
          {series.map((s) => (
            <Scatter
              key={s.name}
              name={s.name}
              data={s.points}
              fill={fillFor(s.name, s.index)}
              isAnimationActive={false}
            />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
