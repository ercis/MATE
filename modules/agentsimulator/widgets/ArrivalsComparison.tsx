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

import { useAgentSimResults } from "../panel/queries";
import { CardShell, COLORS, LegendDots } from "./_kit";

/** New cases started over elapsed time (each log measured from its own start),
 * real vs simulated – shows whether the arrival process is reproduced. */
export default function ArrivalsComparison({
  logId,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const { data, isLoading, isFetching } = useAgentSimResults(logId);
  const series = data?.arrivals?.series ?? [];
  const unit = data?.arrivals?.unit ?? "day";

  return (
    <CardShell
      logId={logId}
      loading={isLoading}
      empty={series.length === 0}
      refreshing={isFetching}
    >
      <div className="flex h-full min-h-0 flex-col gap-1">
        <LegendDots />
        <div className="min-h-0 flex-1">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={series} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" vertical={false} />
              <XAxis
                dataKey="t"
                tick={{ fontSize: 10 }}
                stroke="currentColor"
                className="text-muted-foreground"
                tickFormatter={(v) => `${unit[0]}${v}`}
              />
              <YAxis
                tick={{ fontSize: 10 }}
                stroke="currentColor"
                className="text-muted-foreground"
                allowDecimals={false}
              />
              <Tooltip
                contentStyle={{ fontSize: 12 }}
                labelFormatter={(v) => `${unit} ${v}`}
              />
              <Line
                type="monotone"
                dataKey="real"
                name="Real"
                stroke={COLORS.real}
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="sim"
                name="Simulated"
                stroke={COLORS.sim}
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </CardShell>
  );
}
