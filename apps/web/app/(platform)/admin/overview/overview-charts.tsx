"use client";

import {
  Bar,
  BarChart,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

// recharts-backed chart primitives for the admin overview. Split into their own
// module so the /admin/overview route can lazy-load recharts in an async chunk
// (via next/dynamic in page.tsx) and keep its First Load JS small.

interface DayCount {
  day: string;
  count: number;
}
interface LabelCount {
  label: string;
  count: number;
}

/** "2026-06-17" → "06-17" for compact day-series axis ticks. */
function shortDay(day: string): string {
  return day.length >= 10 ? day.slice(5) : day;
}

export function DayLineChart({
  data,
  valueFormat,
}: {
  data: DayCount[];
  valueFormat?: (value: number) => string;
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <XAxis
          dataKey="day"
          tickFormatter={shortDay}
          tick={{ fontSize: 10 }}
          interval="preserveStartEnd"
          minTickGap={24}
        />
        <YAxis
          tick={{ fontSize: 10 }}
          allowDecimals={false}
          width={valueFormat ? 48 : 28}
          tickFormatter={valueFormat ? (v) => valueFormat(Number(v)) : undefined}
        />
        <Tooltip
          contentStyle={{ fontSize: 12 }}
          formatter={valueFormat ? (v) => valueFormat(Number(v)) : undefined}
        />
        <Line
          type="monotone"
          dataKey="count"
          stroke="currentColor"
          className="text-primary"
          dot={false}
          strokeWidth={2}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function LabelBarChart({ data, horizontal }: { data: LabelCount[]; horizontal?: boolean }) {
  if (horizontal) {
    return (
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <XAxis type="number" tick={{ fontSize: 10 }} allowDecimals={false} />
          <YAxis
            type="category"
            dataKey="label"
            tick={{ fontSize: 10 }}
            width={96}
            interval={0}
          />
          <Tooltip contentStyle={{ fontSize: 12 }} cursor={{ fill: "transparent" }} />
          <Bar dataKey="count" fill="currentColor" className="text-primary" radius={[0, 2, 2, 0]} />
        </BarChart>
      </ResponsiveContainer>
    );
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <XAxis dataKey="label" tick={{ fontSize: 10 }} interval={0} />
        <YAxis tick={{ fontSize: 10 }} allowDecimals={false} width={28} />
        <Tooltip contentStyle={{ fontSize: 12 }} cursor={{ fill: "transparent" }} />
        <Bar dataKey="count" fill="currentColor" className="text-primary" radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
