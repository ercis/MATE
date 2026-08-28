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

import { Skeleton } from "@/components/ui/skeleton";
import { formatDuration } from "@/lib/format";

import { usePerformanceOverTimeTimeseries } from "../panel/queries";

const METRIC_KEY = "avg_cycle_time_s";
const METRIC_LABELS: Record<string, string> = {
  avg_cycle_time_s: "Avg cycle time",
  median_cycle_time_s: "Median cycle time",
  p90_cycle_time_s: "P90 cycle time",
  p95_cycle_time_s: "P95 cycle time",
  min_cycle_time_s: "Min cycle time",
  max_cycle_time_s: "Max cycle time",
  throughput_cases_per_day: "Throughput (cases/day)",
  lead_time_s: "Lead time",
};

// Metrics whose key ends in `_s` are durations in seconds – format them as
// human-readable durations rather than raw second counts.
function isDurationKey(key: string): boolean {
  return key.endsWith("_s");
}

/**
 * A single performance metric over evenly-sized time slices, so you can spot
 * performance drift on a board without opening the module. `config.metric`
 * picks the measure and `config.slices` the bin count.
 */
export default function PerfTrend({
  logId,
  config,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const metricKey = (typeof config?.metric === "string" ? config.metric : METRIC_KEY) as string;
  const slices = typeof config?.slices === "number" ? config.slices : 12;
  const metricLabel = METRIC_LABELS[metricKey] ?? metricKey;
  const isDuration = isDurationKey(metricKey);
  const { data, isLoading, isError } = usePerformanceOverTimeTimeseries(logId, "absolute", {
    slices,
  });

  if (isLoading) return <Skeleton className="h-full min-h-24 w-full" />;

  const points = (data?.slices ?? [])
    .filter((s) => s.metrics != null)
    .map((s) => ({
      label: s.label,
      value: (s.metrics as Record<string, number>)[metricKey],
    }));

  if (isError || points.length === 0) {
    return (
      <div className="flex h-full min-h-24 items-center justify-center text-center text-xs text-muted-foreground">
        No time series for this log yet.
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="mb-1 text-[11px] font-medium text-muted-foreground">{metricLabel}</div>
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ left: 0, right: 8, top: 4, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 9 }}
              stroke="currentColor"
              className="text-muted-foreground"
            />
            <YAxis
              tick={{ fontSize: 10 }}
              width={isDuration ? 56 : 36}
              stroke="currentColor"
              className="text-muted-foreground"
              tickFormatter={isDuration ? (v: number) => formatDuration(v) : undefined}
            />
            <Tooltip
              formatter={(v: number) => [
                isDuration ? formatDuration(v) : v?.toFixed(3),
                metricLabel,
              ]}
              contentStyle={{ fontSize: 12 }}
            />
            <Line
              type="monotone"
              dataKey="value"
              className="stroke-primary"
              strokeWidth={2}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
