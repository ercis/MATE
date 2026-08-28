"use client";

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { colorAt, useEvolution, type Granularity, type LogEvolution } from "../panel/queries";

const OTHER = "Other";

// Keep the first `topN` activities as their own band and fold the rest (incl.
// the backend's own trailing "Other") into a single "Other" band – so the
// widget's `top_n` stays accurate even though the backend bundles up to 12.
function foldMix(mix: LogEvolution["activity_mix"], periods: string[], topN: number) {
  const named = mix.activities.filter((a) => a !== OTHER);
  const kept = named.slice(0, topN);
  const keptSet = new Set(kept);
  const hasOther = mix.activities.length > kept.length;
  const display = hasOther ? [...kept, OTHER] : kept;

  const rows = periods.map((label, p) => {
    const row: Record<string, string | number> = { label };
    let other = 0;
    mix.activities.forEach((act, ai) => {
      const v = mix.series[ai]?.[p] ?? 0;
      if (keptSet.has(act)) row[act] = v;
      else other += v;
    });
    if (hasOther) row[OTHER] = other;
    return row;
  });

  return { display, rows };
}

const fillFor = (act: string, i: number) =>
  act === OTHER ? "var(--muted-foreground)" : colorAt(i);

/**
 * Stacked event volume per activity over time. `config.top_n` controls how many
 * activities keep their own band before the rest fold into "Other".
 */
export default function ActivityMix({
  logId,
  config,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const granularity = (typeof config?.granularity === "string"
    ? config.granularity
    : "auto") as Granularity;
  const topN = typeof config?.top_n === "number" ? config.top_n : 8;
  const { data, isLoading, isError } = useEvolution(logId, granularity);

  const { display, rows } = useMemo(() => {
    if (!data) return { display: [] as string[], rows: [] as Record<string, string | number>[] };
    return foldMix(data.activity_mix, data.periods, topN);
  }, [data, topN]);

  return (
    <ChartFrame
      loading={isLoading}
      empty={isError || rows.length === 0 || display.length === 0}
      emptyText="No activity mix for this log yet."
      legend={display.map((act, i) => (
        <LegendDot key={act} color={fillFor(act, i)} label={act} />
      ))}
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={rows} margin={{ left: 0, right: 8, top: 4, bottom: 4 }}>
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
          {display.map((act, i) => (
            <Area
              key={act}
              type="monotone"
              dataKey={act}
              name={act}
              stackId="mix"
              stroke={fillFor(act, i)}
              fill={fillFor(act, i)}
              fillOpacity={0.5}
              strokeWidth={1}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
import {ChartFrame, LegendDot} from "@/components/dashboards/kit";
