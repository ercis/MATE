"use client";

import { useAgentSimResults, METRIC_ORDER } from "../panel/queries";
import { CardShell, MetricTile } from "./_kit";

/** The five log-distance fidelity measures (lower = closer to the real log). */
export default function FidelityScorecard({
  logId,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const { data, isLoading, isFetching } = useAgentSimResults(logId);
  const ready = data?.status === "ready" && Boolean(data.metrics);

  return (
    <CardShell logId={logId} loading={isLoading} empty={!ready} refreshing={isFetching}>
      <div className="grid h-full grid-cols-2 content-center gap-2 sm:grid-cols-3 md:grid-cols-5">
        {METRIC_ORDER.map((k) => (
          <MetricTile key={k} code={k} cell={data?.metrics?.[k]} />
        ))}
      </div>
    </CardShell>
  );
}
