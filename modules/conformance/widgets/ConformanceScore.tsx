"use client";

import { useConformanceResultsAuto } from "../panel/queries";
import {CardShell, KpiTile} from "@/components/dashboards/kit";

function pct(value: number | null | undefined): string {
  if (value === undefined || value === null) return "–";
  return `${(value * 100).toFixed(1)}%`;
}

/** Compact fitness / precision / conforming-trace scorecard for dashboards. */
export default function ConformanceScore({ logId }: { logId: string }) {
  const { data, isLoading, isError } = useConformanceResultsAuto(logId);
  const ran = Boolean(data?.ran);
  const k = data?.kpis;

  return (
    <CardShell
      loading={isLoading}
      empty={isError || !ran || !k}
      emptyText="Run a conformance check to populate this card."
    >
      {k ? (
        <div className="grid grid-cols-2 gap-2">
          <KpiTile label="Fitness" value={pct(k.log_fitness)} />
          <KpiTile label="Precision" value={pct(k.precision)} />
          <KpiTile label="Conforming" value={`${k.perc_fit_traces.toFixed(1)}%`} />
          <KpiTile
            label="Deviations"
            value={String(k.total_deviations)}
            info="Steps where the recorded process differs from the reference model — either an activity happened that the model doesn't allow, or a required activity was skipped."
          />
        </div>
      ) : null}
    </CardShell>
  );
}
