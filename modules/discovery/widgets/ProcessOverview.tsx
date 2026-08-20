"use client";

import { formatNumber } from "@/lib/format";

import { useDiscoveryDfg } from "../panel/queries";
import { CardShell, KpiTile } from "./_kit";

/** Structural summary of the discovered directly-follows graph. */
export default function ProcessOverview({ logId }: { logId: string }) {
  const { data, isLoading, isError } = useDiscoveryDfg(logId);
  return (
    <CardShell loading={isLoading} empty={isError || !data}>
      {data && (
        <div className="grid grid-cols-2 gap-2">
          <KpiTile label="Activities" value={formatNumber(data.activities.length)} />
          <KpiTile label="Connections" value={formatNumber(data.edges.length)} />
          <KpiTile label="Start activities" value={formatNumber(Object.keys(data.start_activities).length)} />
          <KpiTile label="End activities" value={formatNumber(Object.keys(data.end_activities).length)} />
        </div>
      )}
    </CardShell>
  );
}
