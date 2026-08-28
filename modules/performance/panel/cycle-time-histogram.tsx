"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatDuration } from "@/lib/format";
import type { CycleTimeDistribution } from "./queries";

interface CycleTimeHistogramProps {
  data: CycleTimeDistribution;
}

export function CycleTimeHistogram({ data }: CycleTimeHistogramProps) {
  const chartData = data.buckets.map((b) => ({
    label: formatDuration((b.bucket_min + b.bucket_max) / 2),
    midpoint: (b.bucket_min + b.bucket_max) / 2,
    count: b.count,
  }));

  const labelAtOrAbove = (threshold: number): string | undefined =>
    chartData.find((d) => d.midpoint >= threshold)?.label;

  // Percentiles that fall into the same bucket land on the same x position, so
  // their reference lines and labels would be drawn on top of each other.
  // Collapse them into a single line with a merged label.
  const markers = [
    { name: "median", color: "var(--chart-2)", at: labelAtOrAbove(data.stats.median_cycle_time_s) },
    { name: "p90", color: "var(--chart-4)", at: labelAtOrAbove(data.stats.p90_cycle_time_s) },
    { name: "p95", color: "var(--chart-1)", at: labelAtOrAbove(data.stats.p95_cycle_time_s) },
  ];

  const referenceLines = markers.reduce<{ at: string; names: string[]; color: string }[]>(
    (acc, m) => {
      if (m.at === undefined) return acc;
      const existing = acc.find((r) => r.at === m.at);
      if (existing) existing.names.push(m.name);
      else acc.push({ at: m.at, names: [m.name], color: m.color });
      return acc;
    },
    [],
  );

  return (
    <div className="w-full">
      <ResponsiveContainer width="100%" height={288}>
        {/* top margin leaves room for the ReferenceLine labels, which render above the plot area */}
        <BarChart data={chartData} margin={{ top: 20, right: 32, left: 0, bottom: 16 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
            interval="preserveStartEnd"
            stroke="var(--border)"
          />
          <YAxis
            tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
            stroke="var(--border)"
            allowDecimals={false}
          />
          <Tooltip
            cursor={{ fill: "var(--muted)" }}
            contentStyle={{
              background: "var(--card)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "var(--card-foreground)" }}
            formatter={(value) => [String(value), "cases"]}
          />
          <Bar dataKey="count" fill="var(--primary)" radius={[4, 4, 0, 0]} />
          {referenceLines.map((line) => (
            <ReferenceLine
              key={line.at}
              x={line.at}
              stroke={line.color}
              strokeDasharray="3 3"
              label={{
                value: line.names.join(" · "),
                fill: line.color,
                position: "top",
                fontSize: 10,
              }}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
