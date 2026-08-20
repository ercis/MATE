"use client";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

import { useCdeDrifts } from "../panel/queries";

/** Detected concept drifts with confidence – surfaced from the explainer. */
export default function DriftOverview({
  logId,
  config,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const { data, isLoading, isError } = useCdeDrifts(logId);
  const minConfidence = typeof config?.min_confidence === "number" ? config.min_confidence : 0;

  if (isLoading) return <Skeleton className="h-full min-h-24 w-full" />;
  if (isError) {
    return (
      <div className="flex h-full min-h-24 items-center justify-center text-center text-xs text-muted-foreground">
        Couldn’t load drifts.
      </div>
    );
  }
  if (!data?.ran) {
    return (
      <div className="flex h-full min-h-24 items-center justify-center px-3 text-center text-xs text-muted-foreground">
        Run drift detection in the Concept Drift Explainer module to populate this card.
      </div>
    );
  }

  const drifts = (data.drifts ?? []).filter((d) => d.confidence >= minConfidence);
  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex items-baseline gap-2">
        <span className="text-2xl font-semibold tabular-nums">{drifts.length}</span>
        <span className="text-xs text-muted-foreground">
          drift{drifts.length === 1 ? "" : "s"} across {data.n_windows} windows
        </span>
      </div>
      <div className="min-h-0 flex-1 space-y-1.5 overflow-auto">
        {drifts.length === 0 && (
          <p className="text-xs text-muted-foreground">No drifts detected.</p>
        )}
        {drifts.map((d) => (
          <div
            key={d.drift_key}
            className="flex items-center justify-between gap-2 rounded-md border border-border/60 px-2 py-1.5"
          >
            <div className="min-w-0">
              <Badge variant="secondary" className="capitalize">
                {d.type}
              </Badge>
              <span className="ml-2 text-[11px] text-muted-foreground">
                windows {d.start_window}–{d.end_window}
              </span>
            </div>
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
              {Math.round(d.confidence * 100)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
