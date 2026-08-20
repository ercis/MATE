"use client";

import { Boxes, Layers, Activity } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useOcelOverview } from "@/lib/queries";
import { formatNumber } from "@/lib/format";

export interface OcelOverviewPanelProps {
  logId: string;
}

/** Native object-centric summary shown above the module grid on the Overview
 * tab for OCEL logs – object types & per-type counts, plus activities. */
export function OcelOverviewPanel({ logId }: OcelOverviewPanelProps) {
  const { data, isLoading, isError } = useOcelOverview(logId);

  if (isError) return null;

  if (isLoading || !data) {
    return (
      <div className="rounded-lg border p-4 space-y-3">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <span className="flex items-center gap-1.5 text-muted-foreground">
          <Layers className="h-3.5 w-3.5" />
          <span className="tabular-nums text-foreground">
            {formatNumber(data.object_types_count)}
          </span>{" "}
          object types
        </span>
        <span className="flex items-center gap-1.5 text-muted-foreground">
          <Boxes className="h-3.5 w-3.5" />
          <span className="tabular-nums text-foreground">{formatNumber(data.objects_count)}</span>{" "}
          objects
        </span>
        <span className="flex items-center gap-1.5 text-muted-foreground">
          <Activity className="h-3.5 w-3.5" />
          <span className="tabular-nums text-foreground">{formatNumber(data.events_count)}</span>{" "}
          events
        </span>
      </div>

      <div>
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Object types
        </p>
        <div className="flex flex-wrap gap-2">
          {data.object_types.map((ot) => (
            <Badge key={ot.type} variant="secondary" className="gap-1.5">
              {ot.type}
              <span className="tabular-nums text-muted-foreground">{formatNumber(ot.count)}</span>
            </Badge>
          ))}
        </div>
      </div>

      {data.activities.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Activities ({formatNumber(data.activities.length)})
          </p>
          <div className="flex flex-wrap gap-1.5">
            {data.activities.slice(0, 30).map((a) => (
              <Badge key={a} variant="outline" className="font-mono text-[11px]">
                {a}
              </Badge>
            ))}
            {data.activities.length > 30 && (
              <span className="text-xs text-muted-foreground">
                +{formatNumber(data.activities.length - 30)} more
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
