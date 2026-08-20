"use client";

import { formatNumber } from "@/lib/format";
import { Badge } from "@/components/ui/badge";

import { useOcelSummary } from "../panel/queries";
import { CardShell, KpiTile } from "./_kit";

/** Object types and per-type counts of the OCEL log. */
export default function ObjectTypeSummary({ logId }: { logId: string }) {
  const { data, isLoading, isError } = useOcelSummary(logId);
  return (
    <CardShell loading={isLoading} empty={isError || !data}>
      {data && (
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-2">
            <KpiTile label="Object types" value={formatNumber(data.object_types.length)} />
            <KpiTile label="Objects" value={formatNumber(data.objects_count)} />
            <KpiTile label="Activities" value={formatNumber(data.activities_count)} />
          </div>
          <div className="flex flex-wrap gap-1.5">
            {data.object_types.map((ot) => (
              <Badge key={ot.type} variant="secondary" className="gap-1.5">
                {ot.type}
                <span className="tabular-nums text-muted-foreground">
                  {formatNumber(ot.count)}
                </span>
              </Badge>
            ))}
          </div>
        </div>
      )}
    </CardShell>
  );
}
