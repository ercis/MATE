"use client";

import {
  Area,
  AreaChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ResourceBreakdownSlice, ResourceSample } from "@/lib/api-types";

// recharts-backed chart primitives for the admin system view. Split into their
// own module so the /admin/system route can lazy-load recharts in an async chunk
// (via next/dynamic in page.tsx) and keep its First Load JS small.

function clockTick(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function TimeAreaChart({
  history,
  dataKey,
  yMax,
  yTick,
  tipFormat,
}: {
  history: ResourceSample[];
  dataKey: "cpu_pct" | "mem_used_bytes";
  yMax: number;
  yTick: (v: number) => string;
  tipFormat: (v: number) => string;
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={history} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <XAxis
          dataKey="ts"
          tickFormatter={clockTick}
          tick={{ fontSize: 10 }}
          interval="preserveStartEnd"
          minTickGap={56}
        />
        <YAxis
          domain={[0, yMax]}
          tickFormatter={yTick}
          tick={{ fontSize: 10 }}
          width={44}
          allowDecimals={false}
        />
        <Tooltip
          contentStyle={{ fontSize: 12 }}
          labelFormatter={(l) => clockTick(Number(l))}
          formatter={(v) => [tipFormat(Number(v)), ""]}
        />
        <Area
          type="monotone"
          dataKey={dataKey}
          stroke="currentColor"
          className="text-primary"
          fill="currentColor"
          fillOpacity={0.15}
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function BreakdownDonut({
  slices,
  colors,
  format,
}: {
  slices: ResourceBreakdownSlice[];
  colors: string[];
  format: (v: number) => string;
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Pie
          data={slices}
          dataKey="value"
          nameKey="label"
          innerRadius={45}
          outerRadius={75}
          strokeWidth={1}
          isAnimationActive={false}
        >
          {slices.map((s, i) => (
            <Cell key={`${s.source}-${s.label}`} fill={colors[i]} />
          ))}
        </Pie>
        <Tooltip contentStyle={{ fontSize: 12 }} formatter={(v) => [format(Number(v)), ""]} />
      </PieChart>
    </ResponsiveContainer>
  );
}
