"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useAgentSimResults } from "../panel/queries";
import { CardShell, COLORS, LegendDots } from "./_kit";

/** Case-duration distribution: real (held-out test) vs simulated, per-run avg. */
export default function CycleTimeComparison({
  logId,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const { data, isLoading, isFetching } = useAgentSimResults(logId);
  const bins = data?.cycle_time?.bins ?? [];

  return (
    <CardShell
      logId={logId}
      loading={isLoading}
      empty={bins.length === 0}
      refreshing={isFetching}
    >
      <div className="flex h-full min-h-0 flex-col gap-1">
        <LegendDots />
        <div className="min-h-0 flex-1">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={bins} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" vertical={false} />
              <XAxis
                dataKey="label"
                interval="preserveStartEnd"
                tick={{ fontSize: 9 }}
                stroke="currentColor"
                className="text-muted-foreground"
              />
              <YAxis
                tick={{ fontSize: 10 }}
                stroke="currentColor"
                className="text-muted-foreground"
                allowDecimals={false}
              />
              <Tooltip contentStyle={{ fontSize: 12 }} cursor={{ fillOpacity: 0.08 }} />
              <Bar dataKey="real" name="Real" fill={COLORS.real} radius={[2, 2, 0, 0]} />
              <Bar dataKey="sim" name="Simulated" fill={COLORS.sim} radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </CardShell>
  );
}
