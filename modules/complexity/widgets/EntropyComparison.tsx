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

import { useComplexityMetrics } from "../panel/queries";
import { CardShell } from "./_kit";

/** Side-by-side of the normalized entropy variants (0..1). */
export default function EntropyComparison({ logId }: { logId: string }) {
  const { data, isLoading, isError } = useComplexityMetrics(logId);
  const m = data?.basic;
  const rows = m
    ? [
        { name: "Variant", value: m.normalized_variant_entropy },
        { name: "Sequence", value: m.normalized_sequence_entropy },
        { name: "Seq. linear", value: m.normalized_sequence_entropy_linear },
        { name: "Seq. exp.", value: m.normalized_sequence_entropy_exponential },
      ]
    : [];

  return (
    <CardShell loading={isLoading} empty={isError || !m}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ left: 0, right: 8, top: 8, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" vertical={false} />
          <XAxis
            dataKey="name"
            tick={{ fontSize: 10 }}
            stroke="currentColor"
            className="text-muted-foreground"
          />
          <YAxis
            domain={[0, 1]}
            tick={{ fontSize: 10 }}
            width={32}
            stroke="currentColor"
            className="text-muted-foreground"
          />
          <Tooltip
            formatter={(v: number) => [v.toFixed(3), "Normalized entropy"]}
            contentStyle={{ fontSize: 12 }}
          />
          <Bar dataKey="value" className="fill-primary" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </CardShell>
  );
}
