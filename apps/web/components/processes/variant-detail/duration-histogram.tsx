"use client";

import { useMemo } from "react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatDuration, formatNumber } from "@/lib/format";

export interface DurationHistogramProps {
  counts: number[];
  edges: number[];
}

interface Bin {
  label: string;
  range: string;
  count: number;
}

export function DurationHistogram({ counts, edges }: DurationHistogramProps) {
  const data = useMemo<Bin[]>(() => {
    if (counts.length === 0 || edges.length < 2) return [];
    return counts.map((count, i) => {
      const lo = edges[i];
      const hi = edges[i + 1] ?? edges[i];
      return {
        label: formatDuration(lo),
        range: `${formatDuration(lo)} – ${formatDuration(hi)}`,
        count,
      };
    });
  }, [counts, edges]);

  if (data.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        Not enough cases to build a histogram.
      </p>
    );
  }

  return (
    <div className="h-48 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <XAxis dataKey="label" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 10 }} allowDecimals={false} width={28} />
          <Tooltip
            formatter={(value) => [
              formatNumber(typeof value === "number" ? value : Number(value)),
              "Cases",
            ]}
            labelFormatter={(_, payload) => {
              const first = payload?.[0]?.payload as Bin | undefined;
              return first ? first.range : "";
            }}
            contentStyle={{ fontSize: 12 }}
          />
          <Bar dataKey="count" fill="currentColor" className="text-primary" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
