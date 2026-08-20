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

import { useComplexityTimeseries } from "../panel/queries";

const METRIC_KEY = "normalized_sequence_entropy";
const METRIC_LABELS: Record<string, string> = {
  normalized_sequence_entropy: "Sequence entropy (norm.)",
  normalized_variant_entropy: "Variant entropy (norm.)",
  normalized_sequence_entropy_linear: "Sequence entropy – linear (norm.)",
  normalized_sequence_entropy_exponential: "Sequence entropy – exponential (norm.)",
  lempel_ziv: "Lempel–Ziv",
  magnitude: "Magnitude",
  variety: "Variety",
  pentland_process: "Pentland process",
};

/**
 * A single complexity metric over evenly-sized time slices, so you can spot
 * complexity drift on a board without opening the module. `config.metric`
 * picks the measure and `config.slices` the bin count.
 */
export default function ComplexityTrend({
  logId,
  config,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const metricKey = (typeof config?.metric === "string" ? config.metric : METRIC_KEY) as string;
  const slices = typeof config?.slices === "number" ? config.slices : 12;
  const metricLabel = METRIC_LABELS[metricKey] ?? metricKey;
  const { data, isLoading, isError } = useComplexityTimeseries(logId, "absolute", { slices });

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
              width={36}
              stroke="currentColor"
              className="text-muted-foreground"
            />
            <Tooltip
              formatter={(v: number) => [v?.toFixed(3), metricLabel]}
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
